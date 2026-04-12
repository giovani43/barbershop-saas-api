"""
Blueprint /api/v1/clients — autenticación de clientes (registro + login + me + turnos).
Usa el modelo User existente (tabla users); solo agrega password_hash.
"""
from datetime import timezone
from functools import wraps

import pytz
from flask import Blueprint, current_app, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import User

bp  = Blueprint("clients", __name__)
ART = pytz.timezone("America/Argentina/Buenos_Aires")


# ── Token helpers ──────────────────────────────────────────────────────────────

def _make_client_token(user_id: int) -> str:
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    return s.dumps({"user_id": user_id}, salt="client-v1")


def _verify_client_token(token: str):
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    try:
        data = s.loads(token, salt="client-v1", max_age=86_400 * 30)  # 30 días
        return data.get("user_id")
    except (BadSignature, SignatureExpired):
        return None


def client_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth    = request.headers.get("Authorization", "")
        token   = auth.removeprefix("Bearer ").strip()
        user_id = _verify_client_token(token)
        if not user_id:
            return jsonify({"error": "No autorizado"}), 401
        user = db.session.get(User, user_id)
        if not user:
            return jsonify({"error": "Usuario no encontrado"}), 404
        return f(user, *args, **kwargs)
    return wrapper


# ── POST /register ─────────────────────────────────────────────────────────────

@bp.post("/register")
def register():
    data     = request.get_json() or {}
    nombre   = str(data.get("nombre", "")).strip()
    dni      = str(data.get("dni",    "")).replace(".", "").strip()
    whatsapp = str(data.get("whatsapp", "")).strip()
    password = str(data.get("password", "")).strip()

    if not nombre or not dni or not whatsapp or not password:
        return jsonify({"error": "Nombre, DNI, WhatsApp y contraseña son obligatorios"}), 422
    if len(password) < 6:
        return jsonify({"error": "La contraseña debe tener al menos 6 caracteres"}), 422

    existing = User.query.filter_by(dni=dni).first()
    if existing:
        return jsonify({"error": "Ya existe una cuenta con ese DNI"}), 409

    user = User(
        dni=dni,
        name=nombre,
        whatsapp=whatsapp,
        password_hash=generate_password_hash(password),
    )
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Ya existe una cuenta con ese DNI"}), 409
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Error al registrar", "detail": str(e)}), 500

    token = _make_client_token(user.id)
    return jsonify({
        "token": token,
        "user":  {"id": user.id, "name": user.name, "dni": user.dni, "whatsapp": user.whatsapp},
    }), 201


# ── POST /login ────────────────────────────────────────────────────────────────

@bp.post("/login")
def login():
    data     = request.get_json() or {}
    dni      = str(data.get("dni",      "")).replace(".", "").strip()
    password = str(data.get("password", "")).strip()

    if not dni or not password:
        return jsonify({"error": "DNI y contraseña son obligatorios"}), 422

    user = User.query.filter_by(dni=dni).first()
    if not user:
        return jsonify({"error": "No encontramos una cuenta con ese DNI"}), 401
    if not user.password_hash:
        return jsonify({"error": "Esta cuenta fue creada sin contraseña. Registrate de nuevo."}), 401
    if not check_password_hash(user.password_hash, password):
        return jsonify({"error": "DNI o contraseña incorrectos"}), 401

    token = _make_client_token(user.id)
    return jsonify({
        "token": token,
        "user":  {"id": user.id, "name": user.name, "dni": user.dni, "whatsapp": user.whatsapp},
    })


# ── GET /me ────────────────────────────────────────────────────────────────────

@bp.get("/me")
@client_required
def me(user):
    return jsonify({"id": user.id, "name": user.name, "dni": user.dni, "whatsapp": user.whatsapp})


# ── GET /appointments ──────────────────────────────────────────────────────────

@bp.get("/appointments")
@client_required
def appointments(user):
    rows = db.session.execute(text("""
        SELECT
            a.id::text,
            a.appointment_time,
            a.status,
            a.service_name,
            a.price,
            a.booking_code,
            a.qr_token,
            a.cancelled_at,
            a.verified_at,
            b.name AS barber_nombre
        FROM appointments a
        JOIN barbers b ON b.id = a.barber_id
        WHERE a.user_id = :uid
           OR a.client_id IN (SELECT id FROM clients WHERE dni = :dni)
        ORDER BY a.appointment_time DESC
        LIMIT 50
    """), {"uid": user.id, "dni": user.dni}).mappings().all()

    STATUS_MAP = {
        "booked":      "pendiente",
        "rescheduled": "pendiente",
        "cancelled":   "cancelado",
        "no_show":     "ausente",
        "completed":   "presente",
        "available":   "disponible",
    }

    result = []
    for r in rows:
        appt_utc = r["appointment_time"]
        if appt_utc.tzinfo is None:
            appt_utc = appt_utc.replace(tzinfo=timezone.utc)
        local_t = appt_utc.astimezone(ART)

        # Sólo mostrar si fue reservado alguna vez
        if r["status"] == "available":
            continue

        result.append({
            "id":            r["id"],
            "booking_code":  r["booking_code"],
            "qr_token":      r["qr_token"],
            "barber_nombre": r["barber_nombre"],
            "servicio":      r["service_name"],
            "precio":        float(r["price"]) if r["price"] else 0,
            "fecha":         local_t.strftime("%d/%m/%Y"),
            "hora":          local_t.strftime("%H:%M"),
            "estado":        STATUS_MAP.get(r["status"], r["status"]),
            "estado_raw":    r["status"],
            "cancelled_at":  r["cancelled_at"].isoformat() if r["cancelled_at"] else None,
            "verified_at":   r["verified_at"].isoformat()  if r["verified_at"]  else None,
        })

    return jsonify({"turnos": result})
