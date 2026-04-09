from datetime import datetime, timezone
from functools import wraps

import pytz
from flask import Blueprint, current_app, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import text
from werkzeug.security import check_password_hash

from app.extensions import db
from app.models import Barber

bp  = Blueprint("dashboard", __name__)
ART = pytz.timezone("America/Argentina/Buenos_Aires")


# ── Token helpers ──────────────────────────────────────────────────────────────

def _make_barber_token(barber_id: str) -> str:
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    return s.dumps({"barber_id": barber_id}, salt="barber-v1")


def _verify_barber_token(token: str):
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    try:
        data = s.loads(token, salt="barber-v1", max_age=86_400 * 30)  # 30 días
        return data.get("barber_id")
    except (BadSignature, SignatureExpired):
        return None


def barber_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth      = request.headers.get("Authorization", "")
        token     = auth.removeprefix("Bearer ").strip()
        barber_id = _verify_barber_token(token)
        if not barber_id:
            return jsonify({"error": "No autorizado"}), 401
        barber = db.session.get(Barber, barber_id)
        if not barber or not barber.is_active:
            return jsonify({"error": "Barbero no encontrado"}), 404
        return f(barber, *args, **kwargs)
    return wrapper


# ── POST /login ────────────────────────────────────────────────────────────────

@bp.post("/login")
def barber_login():
    data     = request.get_json() or {}
    slug     = data.get("slug", "").strip().lower()
    password = data.get("password", "").strip()

    if not slug or not password:
        return jsonify({"error": "Slug y contraseña requeridos"}), 422

    barber = Barber.query.filter_by(slug=slug, is_active=True).first()
    if not barber or not barber.password_hash:
        return jsonify({"error": "Usuario o contraseña incorrectos"}), 401

    if not check_password_hash(barber.password_hash, password):
        return jsonify({"error": "Usuario o contraseña incorrectos"}), 401

    token = _make_barber_token(barber.id)
    return jsonify({"token": token, "barber": barber.to_dict()})


# ── GET /me ────────────────────────────────────────────────────────────────────

@bp.get("/me")
@barber_required
def barber_me(barber):
    return jsonify(barber.to_dict())


# ── GET /me/day — turnos de hoy ───────────────────────────────────────────────

@bp.get("/me/day")
@barber_required
def barber_day(barber):
    date_str = request.args.get("date")
    if date_str:
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "Formato de fecha inválido (YYYY-MM-DD)"}), 422
    else:
        target = datetime.now(ART).date()

    # Rango UTC del día en ART
    start_local = ART.localize(datetime(target.year, target.month, target.day, 0, 0, 0))
    end_local   = ART.localize(datetime(target.year, target.month, target.day, 23, 59, 59))
    start_utc   = start_local.astimezone(timezone.utc)
    end_utc     = end_local.astimezone(timezone.utc)

    rows = db.session.execute(text("""
        SELECT
            a.id::text,
            a.appointment_time,
            a.status,
            a.service_name,
            a.price,
            a.booking_code,
            c.full_name  AS client_name,
            c.whatsapp   AS client_wa
        FROM appointments a
        LEFT JOIN clients c ON c.id = a.client_id
        WHERE a.barber_id = :bid
          AND a.appointment_time BETWEEN :start AND :end
        ORDER BY a.appointment_time
    """), {"bid": barber.id, "start": start_utc, "end": end_utc}).mappings().all()

    slots = []
    for r in rows:
        appt_utc = r["appointment_time"]
        if appt_utc.tzinfo is None:
            appt_utc = appt_utc.replace(tzinfo=timezone.utc)
        local_t = appt_utc.astimezone(ART)
        slots.append({
            "id":           r["id"],
            "time":         local_t.strftime("%H:%M"),
            "status":       r["status"],
            "service_name": r["service_name"],
            "price":        float(r["price"]) if r["price"] else 0,
            "booking_code": r["booking_code"],
            "client_name":  r["client_name"],
            "client_wa":    r["client_wa"],
        })

    return jsonify({"date": target.strftime("%d/%m/%Y"), "slots": slots})


# ── Blocked slots ──────────────────────────────────────────────────────────────

@bp.post("/blocked-slots")
@barber_required
def create_blocked_slot(barber):
    data     = request.get_json() or {}
    date_str = (data.get("date") or "").strip()
    time_str = (data.get("time") or "").strip() or None
    all_day  = bool(data.get("all_day", False))
    reason   = (data.get("reason") or "").strip()[:100] or None

    if not date_str:
        return jsonify({"error": "Fecha requerida"}), 422
    if not all_day and not time_str:
        return jsonify({"error": "Indicá un horario o marcá 'Todo el día'"}), 422

    row = db.session.execute(text("""
        INSERT INTO blocked_slots (barber_id, blocked_date, blocked_time, all_day, reason)
        VALUES (:bid, :date, :time, :all_day, :reason)
        RETURNING id, blocked_date::text, blocked_time::text, all_day, reason
    """), {
        "bid":     barber.id,
        "date":    date_str,
        "time":    time_str if not all_day else None,
        "all_day": all_day,
        "reason":  reason,
    }).mappings().first()
    db.session.commit()

    return jsonify({
        "id":      row["id"],
        "date":    row["blocked_date"],
        "time":    row["blocked_time"],
        "all_day": row["all_day"],
        "reason":  row["reason"],
    }), 201


@bp.get("/blocked-slots")
@barber_required
def list_blocked_slots(barber):
    rows = db.session.execute(text("""
        SELECT id, blocked_date::text, blocked_time::text, all_day, reason
        FROM blocked_slots
        WHERE barber_id = :bid AND blocked_date >= CURRENT_DATE
        ORDER BY blocked_date, blocked_time NULLS FIRST
    """), {"bid": barber.id}).mappings().all()

    return jsonify([{
        "id":      r["id"],
        "date":    r["blocked_date"],
        "time":    r["blocked_time"],
        "all_day": r["all_day"],
        "reason":  r["reason"],
    } for r in rows])


@bp.delete("/blocked-slots/<int:slot_id>")
@barber_required
def delete_blocked_slot(barber, slot_id):
    result = db.session.execute(text("""
        DELETE FROM blocked_slots
        WHERE id = :id AND barber_id = :bid
        RETURNING id
    """), {"id": slot_id, "bid": barber.id})

    if not result.fetchone():
        return jsonify({"error": "Bloqueo no encontrado"}), 404

    db.session.commit()
    return jsonify({"ok": True})


# ── Legacy endpoint (backward compat) ─────────────────────────────────────────

@bp.route("/dashboard", methods=["GET"])
def get_dashboard():
    barber_id = request.args.get("barber_id")
    if not barber_id:
        return jsonify({"error": "Falta barber_id"}), 400
    try:
        result = db.session.execute(text("""
            SELECT c.full_name, a.service_name, a.appointment_time, a.price
            FROM appointments a
            JOIN clients c ON a.client_id = c.id
            WHERE a.barber_id = :barber_id
            ORDER BY a.appointment_time DESC
        """), {"barber_id": barber_id})
        rows = result.fetchall()
        reservas = [
            {
                "nombre":       row[0],
                "service_name": row[1],
                "fecha":        row[2].strftime("%d/%m/%Y") if row[2] else "Sin fecha",
                "hora":         row[2].strftime("%H:%M")    if row[2] else "Sin hora",
                "price":        float(row[3]) if row[3] else 0.0,
            }
            for row in rows
        ]
        return jsonify({"reservations": reservas}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
