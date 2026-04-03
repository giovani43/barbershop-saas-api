from flask import Blueprint, request, jsonify
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
import pytz, uuid
from datetime import timedelta

from app.extensions import db

bp = Blueprint("appointments", __name__)
ART = pytz.timezone("America/Argentina/Buenos_Aires")
CANCEL_WINDOW_MINUTES = 90


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


@bp.post("/book")
def book_appointment():
    data = request.get_json()
    required = ["appointment_id", "full_name", "dni", "whatsapp"]
    missing  = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Campos faltantes: {', '.join(missing)}"}), 422

    dni       = str(data["dni"]).replace(".", "").strip()
    whatsapp  = str(data["whatsapp"]).strip()
    full_name = str(data["full_name"]).strip()

    appt = db.session.execute(
        text("SELECT * FROM appointments WHERE id = :id FOR UPDATE"),
        {"id": data["appointment_id"]}
    ).mappings().first()

    if not appt:
        return jsonify({"error": "Turno no encontrado"}), 404
    if appt["status"] != "available":
        return jsonify({"error": "Este turno ya no está disponible"}), 409

    client = db.session.execute(
        text("SELECT id FROM clients WHERE dni = :dni AND barber_id = :bid"),
        {"dni": dni, "bid": str(appt["barber_id"])}
    ).mappings().first()

    if not client:
        result = db.session.execute(text("""
            INSERT INTO clients (full_name, dni, whatsapp, barber_id)
            VALUES (:name, :dni, :wa, :bid)
            RETURNING id
        """), {"name": full_name, "dni": dni, "wa": whatsapp, "bid": str(appt["barber_id"])})
        client_id = result.scalar()
    else:
        client_id = client["id"]

    # Optionally stamp service info from catalogue
    extra_sql    = ""
    extra_params = {}
    service_id   = data.get("service_id")
    if service_id:
        from app.models import Service
        svc = db.session.get(Service, service_id)
        if svc:
            extra_sql    = ", service_name = :svc_name, price = :svc_price, service_id = :svc_id"
            extra_params = {"svc_name": svc.name, "svc_price": float(svc.price), "svc_id": service_id}

    db.session.execute(text(f"""
        UPDATE appointments
        SET status = 'booked', client_id = :client_id, updated_at = NOW() {extra_sql}
        WHERE id = :id
    """), {"client_id": str(client_id), "id": data["appointment_id"], **extra_params})

    # Refresh the appointment to return human-readable confirmation details
    updated = db.session.execute(
        text("SELECT appointment_time, service_name, price, barber_id FROM appointments WHERE id = :id"),
        {"id": data["appointment_id"]}
    ).mappings().first()

    barber_row = db.session.execute(
        text("SELECT name FROM barbers WHERE id = :bid"),
        {"bid": str(updated["barber_id"])}
    ).mappings().first()

    appt_utc = updated["appointment_time"]
    if appt_utc.tzinfo is None:
        appt_utc = appt_utc.replace(tzinfo=timezone.utc)
    local_t = appt_utc.astimezone(ART)

    db.session.commit()
    return jsonify({
        "message": "Turno reservado exitosamente",
        "appointment": {
            "barber_name":  barber_row["name"] if barber_row else "",
            "service_name": updated["service_name"],
            "price":        float(updated["price"]) if updated["price"] else 0,
            "date":         local_t.strftime("%d/%m/%Y"),
            "time":         local_t.strftime("%H:%M"),
        },
    }), 201


@bp.post("/<appointment_id>/cancel")
def cancel_appointment(appointment_id):
    data = request.get_json()
    dni  = str(data.get("dni", "")).replace(".", "").strip()
    if not dni:
        return jsonify({"error": "Se requiere el DNI"}), 400

    appt = db.session.execute(
        text("SELECT * FROM appointments WHERE id = :id FOR UPDATE"),
        {"id": appointment_id}
    ).mappings().first()

    if not appt or appt["status"] != "booked":
        return jsonify({"error": "Turno no encontrado o no está reservado"}), 404

    client = db.session.execute(
        text("SELECT id FROM clients WHERE id = :cid AND dni = :dni"),
        {"cid": str(appt["client_id"]), "dni": dni}
    ).mappings().first()

    if not client:
        return jsonify({"error": "El DNI no corresponde a este turno"}), 403

    now_utc       = datetime.now(timezone.utc)
    appt_time_utc = appt["appointment_time"].replace(tzinfo=timezone.utc)
    minutes_left  = (appt_time_utc - now_utc).total_seconds() / 60

    if minutes_left < CANCEL_WINDOW_MINUTES:
        return jsonify({
            "error":             "Cancelación bloqueada",
            "minutes_remaining": round(minutes_left, 1),
            "message":           f"Faltan solo {int(minutes_left)} min. Contactá al barbero.",
        }), 409

    db.session.execute(text("""
        UPDATE appointments
        SET status = 'cancelled', client_id = NULL, cancelled_at = NOW(), updated_at = NOW()
        WHERE id = :id
    """), {"id": appointment_id})

    db.session.execute(text("""
        INSERT INTO appointments (barber_id, appointment_time, duration_minutes, status, service_name, price)
        VALUES (:bid, :appt_time, :dur, 'available', :service, :price)
        ON CONFLICT (barber_id, appointment_time) DO NOTHING
    """), {
        "bid": str(appt["barber_id"]), "appt_time": appt["appointment_time"],
        "dur": appt["duration_minutes"], "service": appt["service_name"], "price": appt["price"]
    })

    db.session.commit()
    return jsonify({"message": "Turno cancelado. El slot quedó libre."}), 200


@bp.get("/day")
def get_day():
    barber_id = request.args.get("barber_id")
    date_str  = request.args.get("date", datetime.now(ART).strftime("%Y-%m-%d"))
    date      = datetime.strptime(date_str, "%Y-%m-%d").date()

    # Construir el rango del día EN ARGENTINA y convertir a UTC para la query
    start_utc = ART.localize(datetime(date.year, date.month, date.day, 0, 0)).astimezone(timezone.utc)
    end_utc   = ART.localize(datetime(date.year, date.month, date.day, 23, 59)).astimezone(timezone.utc)

    rows = db.session.execute(text("""
        SELECT 
            id::text,
            appointment_time,
            status,
            service_name,
            price
        FROM appointments
        WHERE barber_id = :bid
          AND appointment_time BETWEEN :start AND :end
        ORDER BY appointment_time
    """), {"bid": barber_id, "start": start_utc, "end": end_utc}).mappings().all()

    slots = []
    for r in rows:
        # appointment_time viene de PostgreSQL como datetime naive (sin tzinfo)
        # Le asignamos UTC explícitamente y luego convertimos a Argentina
        appt_utc = r["appointment_time"]
        if appt_utc.tzinfo is None:
            appt_utc = appt_utc.replace(tzinfo=timezone.utc)
        
        local_t = appt_utc.astimezone(ART)  # ← Convertir UTC → Argentina

        slots.append({
            "id":      r["id"],
            "time":    local_t.strftime("%H:%M"),   # ← Hora correcta en ART
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
            "booked":    sum(1 for s in slots if s["status"] == "booked"),
        }
    })
    
@bp.post("/generate-week")
def generate_week():
    from datetime import timedelta
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