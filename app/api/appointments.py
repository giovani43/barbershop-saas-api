import io
import uuid
from datetime import datetime, timezone, timedelta

import pytz
from flask import Blueprint, current_app, jsonify, request, send_file
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import db

bp  = Blueprint("appointments", __name__)
ART = pytz.timezone("America/Argentina/Buenos_Aires")


# ── helpers ────────────────────────────────────────────────────────────────────

def _cancel_window():
    return current_app.config.get("CANCEL_WINDOW_MINUTES", 90)

def _minutes_until(appt_time):
    """Minutes remaining until appointment_time (UTC-aware)."""
    appt_utc = appt_time
    if appt_utc.tzinfo is None:
        appt_utc = appt_utc.replace(tzinfo=timezone.utc)
    return (appt_utc - datetime.now(timezone.utc)).total_seconds() / 60

def _next_booking_code() -> str:
    """Generate next OE-XXXX code, autoincremental."""
    row = db.session.execute(text("""
        SELECT MAX(CAST(SUBSTRING(booking_code FROM 4) AS INTEGER))
        FROM appointments
        WHERE booking_code LIKE 'OE-%'
    """)).scalar()
    next_num = (row or 0) + 1
    return f"OE-{next_num:04d}"


# ── POST /create-slots ─────────────────────────────────────────────────────────

@bp.post("/create-slots")
def create_slots():
    data       = request.get_json()
    barber_id  = data.get("barber_id")
    date_str   = data.get("date")
    start_hour = data.get("start_hour", 9)
    end_hour   = data.get("end_hour", 18)

    if not barber_id or not date_str:
        return jsonify({"error": "Faltan barber_id o date"}), 400

    try:
        date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Formato inválido. Usar YYYY-MM-DD"}), 400

    slots_created = []
    current_hour, current_min = start_hour, 0

    while True:
        local_dt = ART.localize(datetime(date.year, date.month, date.day, current_hour, current_min))
        utc_dt   = local_dt.astimezone(timezone.utc)

        db.session.execute(text("""
            INSERT INTO appointments (barber_id, appointment_time, status, service_name, price)
            VALUES (:barber_id, :appt_time, 'available', 'Corte + Barba', 3500)
            ON CONFLICT (barber_id, appointment_time) DO NOTHING
        """), {"barber_id": barber_id, "appt_time": utc_dt})

        slots_created.append(local_dt.strftime("%H:%M"))
        current_min += 30
        if current_min >= 60:
            current_min -= 60
            current_hour += 1
        if current_hour >= end_hour:
            break

    db.session.commit()
    return jsonify({"message": f"Se crearon {len(slots_created)} slots", "slots": slots_created}), 201


# ── POST /generate-week ────────────────────────────────────────────────────────

@bp.post("/generate-week")
def generate_week():
    data       = request.get_json() or {}
    barber_id  = data.get("barber_id")
    days       = data.get("days", 3)
    start_hour = data.get("start_hour", 9)
    end_hour   = data.get("end_hour", 18)

    if not barber_id:
        return jsonify({"error": "Falta barber_id"}), 400

    today = datetime.now(ART).date()
    total = 0

    for day_offset in range(days):
        target_date = today + timedelta(days=day_offset)
        current_hour, current_min = start_hour, 0

        while True:
            local_dt = ART.localize(datetime(
                target_date.year, target_date.month, target_date.day,
                current_hour, current_min
            ))
            utc_dt = local_dt.astimezone(timezone.utc)

            db.session.execute(text("""
                INSERT INTO appointments (barber_id, appointment_time, status, service_name, price)
                VALUES (:barber_id, :appt_time, 'available', 'Corte + Barba', 3500)
                ON CONFLICT (barber_id, appointment_time) DO NOTHING
            """), {"barber_id": barber_id, "appt_time": utc_dt})

            total += 1
            current_min += 30
            if current_min >= 60:
                current_min -= 60
                current_hour += 1
            if current_hour >= end_hour:
                break

    db.session.commit()
    return jsonify({"message": f"Slots generados para {days} días", "total": total})


# ── POST /book ─────────────────────────────────────────────────────────────────

@bp.post("/book")
def book_appointment():
    data = request.get_json() or {}

    # ── Validar campos requeridos ──────────────────────────────────────────
    required = ["appointment_id", "user_id", "service_id"]
    missing  = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Campos faltantes: {', '.join(missing)}"}), 422

    terms_accepted = data.get("terms_accepted", False)

    # ── 1. Verificar que el usuario existe ────────────────────────────────
    from app.models import User
    user = db.session.get(User, int(data["user_id"]))
    if not user:
        return jsonify({"error": "Usuario no encontrado. Registrate primero."}), 404

    # ── 2. Verificar turno activo por DNI ─────────────────────────────────
    existing = db.session.execute(text("""
        SELECT id FROM appointments
        WHERE status IN ('booked', 'rescheduled')
          AND appointment_time > NOW()
          AND (
              user_id = :uid
              OR client_id IN (SELECT id FROM clients WHERE dni = :dni)
          )
        LIMIT 1
    """), {"uid": user.id, "dni": user.dni}).mappings().first()

    if existing:
        return jsonify({
            "error":          "Ya tenés un turno activo. Cancelalo antes de reservar uno nuevo.",
            "active_appt_id": str(existing["id"]),
        }), 409

    # ── 3. Traer y bloquear el slot ────────────────────────────────────────
    appt = db.session.execute(
        text("SELECT * FROM appointments WHERE id = :id FOR UPDATE"),
        {"id": data["appointment_id"]}
    ).mappings().first()

    if not appt:
        return jsonify({"error": "Turno no encontrado"}), 404
    if appt["status"] != "available":
        return jsonify({"error": "Este turno ya no está disponible"}), 409

    # ── 3b. Verificar que el slot no esté bloqueado ───────────────────────
    appt_utc_chk = appt["appointment_time"]
    if appt_utc_chk.tzinfo is None:
        appt_utc_chk = appt_utc_chk.replace(tzinfo=timezone.utc)
    local_chk = appt_utc_chk.astimezone(ART)
    is_blocked = db.session.execute(text("""
        SELECT 1 FROM blocked_slots
        WHERE barber_id    = :bid
          AND blocked_date = :date
          AND (all_day = TRUE OR blocked_time = :time::time)
        LIMIT 1
    """), {
        "bid":  str(appt["barber_id"]),
        "date": local_chk.date().isoformat(),
        "time": local_chk.strftime("%H:%M"),
    }).first()

    if is_blocked:
        return jsonify({"error": "Este horario no está disponible"}), 409

    # ── 4. Crear o recuperar client (para compat. con cancel/reschedule) ──
    client = db.session.execute(
        text("SELECT id FROM clients WHERE dni = :dni AND barber_id = :bid"),
        {"dni": user.dni, "bid": str(appt["barber_id"])}
    ).mappings().first()

    if not client:
        result = db.session.execute(text("""
            INSERT INTO clients (full_name, dni, whatsapp, barber_id)
            VALUES (:name, :dni, :wa, :bid)
            RETURNING id
        """), {"name": user.name, "dni": user.dni, "wa": user.whatsapp,
               "bid": str(appt["barber_id"])})
        client_id = result.scalar()
    else:
        client_id = client["id"]

    # ── 4. Datos del servicio ──────────────────────────────────────────────
    service_sql    = ""
    service_params = {}
    service_id     = data.get("service_id")
    if service_id:
        from app.models import Service
        svc = db.session.get(Service, service_id)
        if svc:
            # Validate service belongs to the same shop as the barber
            barber_shop = db.session.execute(
                text("SELECT shop_id FROM barbers WHERE id = :bid"),
                {"bid": str(appt["barber_id"])}
            ).scalar()
            if barber_shop and svc.shop_id != barber_shop:
                return jsonify({"error": "El servicio no corresponde a esta barbería"}), 422
            service_sql    = ", service_name = :svc_name, price = :svc_price, service_id = :svc_id"
            service_params = {
                "svc_name":  svc.name,
                "svc_price": float(svc.price),
                "svc_id":    service_id,
            }

    # ── 5. Generar qr_token y booking_code ────────────────────────────────
    qr_token     = str(uuid.uuid4())
    booking_code = _next_booking_code()

    # ── 6. terms_accepted_at ──────────────────────────────────────────────
    terms_at_sql = ", terms_accepted_at = NOW()" if terms_accepted else ""

    # ── 7. UPDATE del slot ─────────────────────────────────────────────────
    db.session.execute(text(f"""
        UPDATE appointments
        SET status            = 'booked',
            client_id         = :client_id,
            user_id           = :user_id,
            whatsapp_number   = :wa,
            qr_token          = :qr_token,
            booking_code      = :booking_code,
            rescheduled_count = 0,
            updated_at        = NOW()
            {service_sql}
            {terms_at_sql}
        WHERE id = :id
    """), {
        "client_id":  str(client_id),
        "user_id":    user.id,
        "wa":         user.whatsapp,
        "qr_token":   qr_token,
        "booking_code": booking_code,
        "id":         data["appointment_id"],
        **service_params,
    })

    # ── 8. Leer datos para la respuesta ───────────────────────────────────
    updated = db.session.execute(text("""
        SELECT appointment_time, service_name, price, barber_id
        FROM appointments WHERE id = :id
    """), {"id": data["appointment_id"]}).mappings().first()

    barber_row = db.session.execute(
        text("SELECT name FROM barbers WHERE id = :bid"),
        {"bid": str(updated["barber_id"])}
    ).mappings().first()

    appt_utc = updated["appointment_time"]
    if appt_utc.tzinfo is None:
        appt_utc = appt_utc.replace(tzinfo=timezone.utc)
    local_t = appt_utc.astimezone(ART)

    db.session.commit()

    price_val       = float(updated["price"]) if updated["price"] else 0
    charge_pct      = current_app.config.get("ABSENCE_CHARGE_PERCENT", 30)
    absence_fee     = round(price_val * charge_pct / 100)
    cancel_window   = current_app.config.get("CANCEL_WINDOW_MINUTES", 90)
    max_reschedules = current_app.config.get("MAX_RESCHEDULES", 1)
    minutes_left    = _minutes_until(appt_utc)
    can_cancel      = minutes_left > cancel_window
    can_reschedule  = can_cancel  # rescheduled_count is 0 for a new booking

    return jsonify({
        "message": "Turno reservado exitosamente",
        "appointment": {
            "id":               data["appointment_id"],
            "booking_code":     booking_code,
            "qr_token":         qr_token,
            "barber_name":      barber_row["name"] if barber_row else "",
            "service_name":     updated["service_name"],
            "price":            price_val,
            "absence_fee":      absence_fee,
            "date":             local_t.strftime("%d/%m/%Y"),
            "time":             local_t.strftime("%H:%M"),
            "can_cancel":       can_cancel,
            "can_reschedule":   can_reschedule,
            "rescheduled_count": 0,
        },
    }), 201


# ── GET /<id>/qr ───────────────────────────────────────────────────────────────

@bp.get("/<appointment_id>/qr")
def get_qr(appointment_id):
    """Devuelve un PNG con el QR del turno."""
    row = db.session.execute(
        text("SELECT qr_token, booking_code FROM appointments WHERE id = :id"),
        {"id": appointment_id}
    ).mappings().first()

    if not row or not row["qr_token"]:
        return jsonify({"error": "Turno no encontrado o sin QR"}), 404

    import qrcode
    frontend_url = current_app.config.get("FRONTEND_URL", "")
    qr_data = f"{frontend_url}/admin/verify/{row['qr_token']}"

    qr   = qrcode.QRCode(box_size=8, border=3)
    qr.add_data(qr_data)
    qr.make(fit=True)
    img  = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


# ── GET /<id> — detalle público del turno ────────────────────────────────────

@bp.get("/<appointment_id>")
def get_appointment(appointment_id):
    """Detalle público de un turno (para la pantalla de éxito/cancelación)."""
    row = db.session.execute(text("""
        SELECT
            a.id::text,
            a.appointment_time,
            a.status,
            a.service_name,
            a.price,
            a.booking_code,
            a.qr_token,
            a.rescheduled_count,
            a.whatsapp_number,
            b.name AS barber_name
        FROM appointments a
        JOIN barbers b ON b.id = a.barber_id
        WHERE a.id = :id
    """), {"id": appointment_id}).mappings().first()

    if not row:
        return jsonify({"error": "Turno no encontrado"}), 404

    appt_utc = row["appointment_time"]
    if appt_utc.tzinfo is None:
        appt_utc = appt_utc.replace(tzinfo=timezone.utc)
    local_t = appt_utc.astimezone(ART)

    minutes_left  = _minutes_until(row["appointment_time"])
    can_cancel    = minutes_left > _cancel_window()
    max_reschedules = current_app.config.get("MAX_RESCHEDULES", 1)
    can_reschedule  = can_cancel and (row["rescheduled_count"] or 0) < max_reschedules

    price_val   = float(row["price"]) if row["price"] else 0
    charge_pct  = current_app.config.get("ABSENCE_CHARGE_PERCENT", 30)
    absence_fee = round(price_val * charge_pct / 100)

    return jsonify({
        "id":               row["id"],
        "booking_code":     row["booking_code"],
        "qr_token":         row["qr_token"],
        "barber_name":      row["barber_name"],
        "service_name":     row["service_name"],
        "price":            price_val,
        "absence_fee":      absence_fee,
        "date":             local_t.strftime("%d/%m/%Y"),
        "time":             local_t.strftime("%H:%M"),
        "status":           row["status"],
        "can_cancel":       can_cancel,
        "can_reschedule":   can_reschedule,
        "rescheduled_count": row["rescheduled_count"] or 0,
        "whatsapp_number":  row["whatsapp_number"],
    })


# ── GET /by-whatsapp — turno activo por número de WhatsApp ───────────────────

@bp.get("/by-whatsapp")
def get_by_whatsapp():
    """Busca el turno activo (booked/rescheduled) de un número de WhatsApp."""
    wa = (request.args.get("wa") or "").strip()
    if not wa:
        return jsonify({"error": "Falta el parámetro wa"}), 422

    row = db.session.execute(text("""
        SELECT
            a.id::text,
            a.appointment_time,
            a.status,
            a.service_name,
            a.price,
            a.booking_code,
            a.qr_token,
            a.rescheduled_count,
            a.whatsapp_number,
            b.id::text  AS barber_id,
            b.name AS barber_name
        FROM appointments a
        JOIN barbers b ON b.id = a.barber_id
        WHERE a.whatsapp_number = :wa
          AND a.status IN ('booked', 'rescheduled')
        ORDER BY a.appointment_time ASC
        LIMIT 1
    """), {"wa": wa}).mappings().first()

    if not row:
        return jsonify({"error": "No tenés un turno activo"}), 404

    appt_utc = row["appointment_time"]
    if appt_utc.tzinfo is None:
        appt_utc = appt_utc.replace(tzinfo=timezone.utc)
    local_t = appt_utc.astimezone(ART)

    minutes_left    = _minutes_until(row["appointment_time"])
    can_cancel      = minutes_left > _cancel_window()
    max_reschedules = current_app.config.get("MAX_RESCHEDULES", 1)
    can_reschedule  = can_cancel and (row["rescheduled_count"] or 0) < max_reschedules

    price_val   = float(row["price"]) if row["price"] else 0
    charge_pct  = current_app.config.get("ABSENCE_CHARGE_PERCENT", 30)
    absence_fee = round(price_val * charge_pct / 100)

    return jsonify({
        "id":               row["id"],
        "booking_code":     row["booking_code"],
        "barber_id":        row["barber_id"],
        "barber_name":      row["barber_name"],
        "service_name":     row["service_name"],
        "price":            price_val,
        "absence_fee":      absence_fee,
        "date":             local_t.strftime("%d/%m/%Y"),
        "time":             local_t.strftime("%H:%M"),
        "status":           row["status"],
        "can_cancel":       can_cancel,
        "can_reschedule":   can_reschedule,
        "rescheduled_count": row["rescheduled_count"] or 0,
    })


# ── GET /by-token/<qr_token> — verificación desde panel admin ────────────────

@bp.get("/by-token/<qr_token>")
def get_by_token(qr_token):
    """El barbero escanea el QR; retorna el detalle del turno."""
    row = db.session.execute(text("""
        SELECT
            a.id::text,
            a.appointment_time,
            a.status,
            a.service_name,
            a.price,
            a.booking_code,
            a.rescheduled_count,
            b.name  AS barber_name,
            c.full_name AS client_name,
            c.whatsapp  AS client_wa
        FROM appointments a
        JOIN barbers b ON b.id = a.barber_id
        LEFT JOIN clients c ON c.id = a.client_id
        WHERE a.qr_token = :token
    """), {"token": qr_token}).mappings().first()

    if not row:
        return jsonify({"error": "QR inválido"}), 404

    appt_utc = row["appointment_time"]
    if appt_utc.tzinfo is None:
        appt_utc = appt_utc.replace(tzinfo=timezone.utc)
    local_t = appt_utc.astimezone(ART)

    return jsonify({
        "id":               row["id"],
        "booking_code":     row["booking_code"],
        "barber_name":      row["barber_name"],
        "client_name":      row["client_name"],
        "client_wa":        row["client_wa"],
        "service_name":     row["service_name"],
        "price":            float(row["price"]) if row["price"] else 0,
        "date":             local_t.strftime("%d/%m/%Y"),
        "time":             local_t.strftime("%H:%M"),
        "status":           row["status"],
        "rescheduled_count": row["rescheduled_count"] or 0,
    })


# ── POST /by-token/<qr_token>/complete — marcar turno como presente ───────────

@bp.post("/by-token/<qr_token>/complete")
def complete_by_token(qr_token):
    """El barbero escanea el QR y marca el turno como completado."""
    row = db.session.execute(
        text("SELECT id, status FROM appointments WHERE qr_token = :token"),
        {"token": qr_token}
    ).mappings().first()

    if not row:
        return jsonify({"error": "QR inválido"}), 404

    if row["status"] not in ("booked", "rescheduled"):
        return jsonify({"error": f"El turno no se puede completar (estado: {row['status']})"}), 400

    db.session.execute(
        text("UPDATE appointments SET status = 'completed', qr_token = NULL WHERE id = :id"),
        {"id": row["id"]}
    )
    db.session.commit()
    return jsonify({"ok": True, "message": "Turno marcado como completado"})


# ── POST /<id>/cancel ─────────────────────────────────────────────────────────

@bp.post("/<appointment_id>/cancel")
def cancel_appointment(appointment_id):
    data = request.get_json() or {}
    dni  = str(data.get("dni", "")).replace(".", "").strip()
    if not dni:
        return jsonify({"error": "Se requiere el DNI"}), 400

    appt = db.session.execute(
        text("SELECT * FROM appointments WHERE id = :id FOR UPDATE"),
        {"id": appointment_id}
    ).mappings().first()

    if not appt or appt["status"] not in ("booked", "rescheduled"):
        return jsonify({"error": "Turno no encontrado o no está reservado"}), 404

    client = db.session.execute(
        text("SELECT id FROM clients WHERE id = :cid AND dni = :dni"),
        {"cid": str(appt["client_id"]), "dni": dni}
    ).mappings().first()

    if not client:
        return jsonify({"error": "El DNI no corresponde a este turno"}), 403

    minutes_left = _minutes_until(appt["appointment_time"])
    window       = _cancel_window()

    if minutes_left < window:
        price_val   = float(appt["price"]) if appt["price"] else 0
        charge_pct  = current_app.config.get("ABSENCE_CHARGE_PERCENT", 30)
        absence_fee = round(price_val * charge_pct / 100)
        return jsonify({
            "error":             "Cancelación bloqueada",
            "minutes_remaining": round(minutes_left, 1),
            "absence_fee":       absence_fee,
            "message": (
                f"Faltan solo {int(minutes_left)} min. "
                f"La cancelación fuera de término genera un cargo de ${absence_fee:,}."
            ),
        }), 403

    # Liberar el slot: vuelve a 'available' (sin recrear fila)
    db.session.execute(text("""
        UPDATE appointments
        SET status          = 'available',
            client_id       = NULL,
            whatsapp_number = NULL,
            qr_token        = NULL,
            booking_code    = NULL,
            rescheduled_count = 0,
            cancelled_at    = NOW(),
            updated_at      = NOW()
        WHERE id = :id
    """), {"id": appointment_id})

    db.session.commit()
    return jsonify({"message": "Turno cancelado. El slot quedó libre."}), 200


# ── POST /<id>/reschedule ─────────────────────────────────────────────────────

@bp.post("/<appointment_id>/reschedule")
def reschedule_appointment(appointment_id):
    """
    Body: { dni, new_slot_id }
    Mueve la reserva al nuevo slot si:
      - Faltan >90 min al turno original
      - rescheduled_count < MAX_RESCHEDULES
    """
    data        = request.get_json() or {}
    dni         = str(data.get("dni", "")).replace(".", "").strip()
    new_slot_id = data.get("new_slot_id")

    if not dni or not new_slot_id:
        return jsonify({"error": "Se requieren dni y new_slot_id"}), 400

    # Traer turno original
    appt = db.session.execute(
        text("SELECT * FROM appointments WHERE id = :id FOR UPDATE"),
        {"id": appointment_id}
    ).mappings().first()

    if not appt or appt["status"] not in ("booked", "rescheduled"):
        return jsonify({"error": "Turno no encontrado o no está reservado"}), 404

    # Verificar DNI
    client = db.session.execute(
        text("SELECT id FROM clients WHERE id = :cid AND dni = :dni"),
        {"cid": str(appt["client_id"]), "dni": dni}
    ).mappings().first()

    if not client:
        return jsonify({"error": "El DNI no corresponde a este turno"}), 403

    # Verificar ventana de tiempo
    minutes_left = _minutes_until(appt["appointment_time"])
    window       = _cancel_window()
    if minutes_left < window:
        return jsonify({
            "error":   "Reprogramación bloqueada",
            "message": f"Solo podés reprogramar con más de {window} minutos de anticipación.",
        }), 403

    # Verificar límite de reprogramaciones
    max_reschedules = current_app.config.get("MAX_RESCHEDULES", 1)
    if (appt["rescheduled_count"] or 0) >= max_reschedules:
        return jsonify({
            "error":   "Límite de reprogramaciones alcanzado",
            "message": "Solo se permite 1 reprogramación por reserva.",
        }), 403

    # Traer y bloquear nuevo slot
    new_slot = db.session.execute(
        text("SELECT * FROM appointments WHERE id = :id FOR UPDATE"),
        {"id": new_slot_id}
    ).mappings().first()

    if not new_slot or new_slot["status"] != "available":
        return jsonify({"error": "El nuevo turno no está disponible"}), 409

    # Liberar slot original
    db.session.execute(text("""
        UPDATE appointments
        SET status = 'available', client_id = NULL, whatsapp_number = NULL,
            qr_token = NULL, booking_code = NULL, rescheduled_count = 0,
            cancelled_at = NOW(), updated_at = NOW()
        WHERE id = :id
    """), {"id": appointment_id})

    # Ocupar nuevo slot con los datos del cliente + incrementar rescheduled_count
    db.session.execute(text("""
        UPDATE appointments
        SET status            = 'rescheduled',
            client_id         = :client_id,
            whatsapp_number   = :wa,
            qr_token          = :qr_token,
            booking_code      = :booking_code,
            rescheduled_count = :rc,
            service_id        = :svc_id,
            service_name      = :svc_name,
            price             = :price,
            updated_at        = NOW()
        WHERE id = :new_id
    """), {
        "client_id":    str(appt["client_id"]),
        "wa":           appt["whatsapp_number"],
        "qr_token":     appt["qr_token"],
        "booking_code": appt["booking_code"],
        "rc":           (appt["rescheduled_count"] or 0) + 1,
        "svc_id":       appt["service_id"],
        "svc_name":     appt["service_name"],
        "price":        appt["price"],
        "new_id":       new_slot_id,
    })

    db.session.commit()

    # Devolver detalle del nuevo slot
    updated = db.session.execute(text("""
        SELECT a.appointment_time, a.service_name, a.price, b.name AS barber_name,
               a.booking_code, a.qr_token
        FROM appointments a JOIN barbers b ON b.id = a.barber_id
        WHERE a.id = :id
    """), {"id": new_slot_id}).mappings().first()

    appt_utc = updated["appointment_time"]
    if appt_utc.tzinfo is None:
        appt_utc = appt_utc.replace(tzinfo=timezone.utc)
    local_t = appt_utc.astimezone(ART)

    return jsonify({
        "message": "Turno reprogramado exitosamente.",
        "appointment": {
            "id":           new_slot_id,
            "booking_code": updated["booking_code"],
            "qr_token":     updated["qr_token"],
            "barber_name":  updated["barber_name"],
            "service_name": updated["service_name"],
            "price":        float(updated["price"]) if updated["price"] else 0,
            "date":         local_t.strftime("%d/%m/%Y"),
            "time":         local_t.strftime("%H:%M"),
        },
    }), 200


# ── GET /day ──────────────────────────────────────────────────────────────────

@bp.get("/day")
def get_day():
    barber_id = request.args.get("barber_id")
    date_str  = request.args.get("date", datetime.now(ART).strftime("%Y-%m-%d"))
    date      = datetime.strptime(date_str, "%Y-%m-%d").date()

    start_utc = ART.localize(datetime(date.year, date.month, date.day,  0,  0)).astimezone(timezone.utc)
    end_utc   = ART.localize(datetime(date.year, date.month, date.day, 23, 59)).astimezone(timezone.utc)

    # Fetch blocked slots for this barber/date
    blocked_rows = db.session.execute(text("""
        SELECT blocked_time, all_day FROM blocked_slots
        WHERE barber_id = :bid AND blocked_date = :date
    """), {"bid": barber_id, "date": date.isoformat()}).mappings().all()

    blocked_all_day = any(b["all_day"] for b in blocked_rows)
    blocked_times   = set()
    for b in blocked_rows:
        if not b["all_day"] and b["blocked_time"]:
            t = b["blocked_time"]
            blocked_times.add(f"{t.hour:02d}:{t.minute:02d}")

    rows = db.session.execute(text("""
        SELECT id::text, appointment_time, status, service_name, price
        FROM appointments
        WHERE barber_id = :bid
          AND appointment_time BETWEEN :start AND :end
        ORDER BY appointment_time
    """), {"bid": barber_id, "start": start_utc, "end": end_utc}).mappings().all()

    slots = []
    for r in rows:
        appt_utc = r["appointment_time"]
        if appt_utc.tzinfo is None:
            appt_utc = appt_utc.replace(tzinfo=timezone.utc)
        local_t      = appt_utc.astimezone(ART)
        local_time   = local_t.strftime("%H:%M")
        is_blocked   = blocked_all_day or local_time in blocked_times

        # Skip available slots that are blocked
        if r["status"] == "available" and is_blocked:
            continue

        slots.append({
            "id":      r["id"],
            "time":    local_time,
            "date":    local_t.strftime("%d/%m/%Y"),
            "status":  r["status"],
            "service": r["service_name"],
            "price":   float(r["price"]) if r["price"] else 0,
        })

    return jsonify({
        "date":  date_str,
        "slots": slots,
        "stats": {
            "total":     len(slots),
            "available": sum(1 for s in slots if s["status"] == "available"),
            "booked":    sum(1 for s in slots if s["status"] in ("booked", "rescheduled")),
        },
    })
