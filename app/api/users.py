from datetime import timezone

import pytz
from flask import Blueprint, jsonify, request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import User

bp  = Blueprint("users", __name__)
ART = pytz.timezone("America/Argentina/Buenos_Aires")


# ── POST /register ─────────────────────────────────────────────────────────────

@bp.post("/register")
def register():
    data     = request.get_json() or {}
    dni      = str(data.get("dni",      "")).replace(".", "").strip()
    name     = str(data.get("name",     "")).strip()
    whatsapp = str(data.get("whatsapp", "")).strip()

    if not dni or not name or not whatsapp:
        return jsonify({"error": "DNI, nombre y WhatsApp son obligatorios"}), 422

    existing = User.query.filter_by(dni=dni).first()
    if existing:
        return jsonify({"error": "Ya existe una cuenta con ese DNI", "user": existing.to_dict()}), 409

    user = User(dni=dni, name=name, whatsapp=whatsapp)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = User.query.filter_by(dni=dni).first()
        return jsonify({"error": "Ya existe una cuenta con ese DNI", "user": existing.to_dict()}), 409

    return jsonify({"user": user.to_dict()}), 201


# ── GET /by-dni ────────────────────────────────────────────────────────────────

@bp.get("/by-dni")
def by_dni():
    dni = str(request.args.get("dni", "")).replace(".", "").strip()
    if not dni:
        return jsonify({"error": "Falta el parámetro dni"}), 422

    user = User.query.filter_by(dni=dni).first()
    if not user:
        return jsonify({"error": "No encontramos una cuenta con ese DNI"}), 404

    # ── Turno activo ───────────────────────────────────────────────────────────
    active = db.session.execute(text("""
        SELECT
            a.id::text,
            a.appointment_time,
            a.status,
            a.service_name,
            a.price,
            a.booking_code,
            a.rescheduled_count,
            a.user_id,
            a.barber_id::text,
            b.name AS barber_name
        FROM appointments a
        JOIN barbers b ON b.id = a.barber_id
        WHERE a.status IN ('booked', 'rescheduled')
          AND a.appointment_time > NOW()
          AND (
              a.user_id = :uid
              OR a.client_id IN (
                  SELECT id FROM clients WHERE dni = :dni
              )
          )
        ORDER BY a.appointment_time ASC
        LIMIT 1
    """), {"uid": user.id, "dni": dni}).mappings().first()

    active_appt = None
    if active:
        from flask import current_app
        appt_utc = active["appointment_time"]
        if appt_utc.tzinfo is None:
            appt_utc = appt_utc.replace(tzinfo=timezone.utc)
        local_t      = appt_utc.astimezone(ART)
        minutes_left = (appt_utc - __import__("datetime").datetime.now(timezone.utc)).total_seconds() / 60
        cancel_win   = current_app.config.get("CANCEL_WINDOW_MINUTES", 90)
        max_resched  = current_app.config.get("MAX_RESCHEDULES", 1)
        can_cancel   = minutes_left > cancel_win
        can_resched  = can_cancel and (active["rescheduled_count"] or 0) < max_resched
        price_val    = float(active["price"]) if active["price"] else 0
        absence_fee  = round(price_val * current_app.config.get("ABSENCE_CHARGE_PERCENT", 30) / 100)

        active_appt = {
            "id":               active["id"],
            "booking_code":     active["booking_code"],
            "barber_id":        active["barber_id"],
            "barber_name":      active["barber_name"],
            "service_name":     active["service_name"],
            "price":            price_val,
            "absence_fee":      absence_fee,
            "date":             local_t.strftime("%d/%m/%Y"),
            "time":             local_t.strftime("%H:%M"),
            "status":           active["status"],
            "can_cancel":       can_cancel,
            "can_reschedule":   can_resched,
            "rescheduled_count": active["rescheduled_count"] or 0,
        }

    # ── Historial (completed / cancelled / no_show) ────────────────────────────
    history_rows = db.session.execute(text("""
        SELECT
            a.id::text,
            a.appointment_time,
            a.status,
            a.service_name,
            a.price,
            a.booking_code,
            b.name AS barber_name
        FROM appointments a
        JOIN barbers b ON b.id = a.barber_id
        WHERE a.status IN ('completed', 'cancelled', 'no_show')
          AND (
              a.user_id = :uid
              OR a.client_id IN (
                  SELECT id FROM clients WHERE dni = :dni
              )
          )
        ORDER BY a.appointment_time DESC
        LIMIT 50
    """), {"uid": user.id, "dni": dni}).mappings().all()

    history = []
    for r in history_rows:
        appt_utc = r["appointment_time"]
        if appt_utc.tzinfo is None:
            appt_utc = appt_utc.replace(tzinfo=timezone.utc)
        local_t = appt_utc.astimezone(ART)
        history.append({
            "id":           r["id"],
            "booking_code": r["booking_code"],
            "barber_name":  r["barber_name"],
            "service_name": r["service_name"],
            "price":        float(r["price"]) if r["price"] else 0,
            "date":         local_t.strftime("%d/%m/%Y"),
            "time":         local_t.strftime("%H:%M"),
            "status":       r["status"],
        })

    return jsonify({
        "user":          user.to_dict(),
        "active_appt":   active_appt,
        "history":       history,
    })
