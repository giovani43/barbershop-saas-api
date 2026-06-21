import io
import logging
from datetime import datetime, timezone
from functools import wraps

import openpyxl
import pytz
from flask import Blueprint, current_app, jsonify, request, send_file
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import text
from werkzeug.security import check_password_hash

from app.extensions import db
from app.models import Barber

logger = logging.getLogger(__name__)

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
    try:
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

        token = _make_barber_token(str(barber.id))
        return jsonify({"token": token, "barber": barber.to_dict()})
    except Exception as e:
        import traceback
        return jsonify({"error": "internal", "detail": str(e),
                        "trace": traceback.format_exc()}), 500


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

    logger.info("[barber_day] barber_id=%s date=%s", barber.id, target)

    rows = db.session.execute(text("""
        SELECT
            a.id::text,
            a.appointment_time,
            a.status,
            a.service_name,
            a.price,
            a.booking_code,
            a.absence_charge_sent,
            a.absence_charge_amount,
            a.penalty_amount,
            COALESCE(a.penalty_paid, FALSE)         AS penalty_paid,
            COALESCE(c.full_name, u.name)           AS client_name,
            COALESCE(c.whatsapp, a.whatsapp_number) AS client_wa,
            COALESCE(c.dni, u.dni)                  AS client_dni
        FROM appointments a
        LEFT JOIN clients c ON c.id = a.client_id
        LEFT JOIN users   u ON u.id = a.user_id
        WHERE a.barber_id = :bid
          AND DATE(a.appointment_time AT TIME ZONE 'America/Argentina/Buenos_Aires') = :date
        ORDER BY a.appointment_time
    """), {"bid": barber.id, "date": target}).mappings().all()

    logger.info("[barber_day] returned %d rows: %s", len(rows), [r["status"] for r in rows])

    slots = []
    for r in rows:
        appt_utc = r["appointment_time"]
        if appt_utc.tzinfo is None:
            appt_utc = appt_utc.replace(tzinfo=timezone.utc)
        local_t = appt_utc.astimezone(ART)
        slots.append({
            "id":                    r["id"],
            "time":                  local_t.strftime("%H:%M"),
            "status":                r["status"],
            "service_name":          r["service_name"],
            "price":                 float(r["price"]) if r["price"] else 0,
            "booking_code":          r["booking_code"],
            "absence_charge_sent":   bool(r["absence_charge_sent"]),
            "absence_charge_amount": int(r["absence_charge_amount"] or 0),
            "penalty_amount":        float(r["penalty_amount"] or 0),
            "penalty_paid":          bool(r["penalty_paid"]),
            "client_name":           r["client_name"],
            "client_wa":             r["client_wa"],
            "client_dni":            r["client_dni"],
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
    return jsonify({"success": True})


# ── Cobrar ausencia ───────────────────────────────────────────────────────────

@bp.post("/appointments/<appt_id>/charge-absence")
@barber_required
def charge_absence(barber, appt_id):
    row = db.session.execute(text("""
        SELECT id, price, status, absence_charge_sent
        FROM appointments
        WHERE id = :id AND barber_id = :bid
    """), {"id": appt_id, "bid": barber.id}).mappings().first()

    if not row:
        return jsonify({"error": "Turno no encontrado"}), 404
    if row["absence_charge_sent"]:
        return jsonify({"error": "El cargo ya fue registrado"}), 409

    charge = round(float(row["price"] or 0) * 0.30)

    db.session.execute(text("""
        UPDATE appointments
        SET absence_charge_sent   = TRUE,
            absence_charge_amount = :charge,
            status                = 'no_show'
        WHERE id = :id
    """), {"charge": charge, "id": appt_id})
    db.session.commit()

    return jsonify({"ok": True, "charge": charge})


# ── PATCH /appointments/<id>/penalty-paid ─────────────────────────────────────

@bp.patch("/appointments/<appt_id>/penalty-paid")
@barber_required
def mark_penalty_paid(barber, appt_id):
    row = db.session.execute(text("""
        SELECT id, status, penalty_paid
        FROM appointments
        WHERE id = :id AND barber_id = :bid
    """), {"id": appt_id, "bid": barber.id}).mappings().first()

    if not row:
        return jsonify({"error": "Turno no encontrado"}), 404
    if row["status"] != "no_show":
        return jsonify({"error": "El turno no está en estado ausente"}), 400
    if row["penalty_paid"]:
        return jsonify({"error": "La penalidad ya fue registrada como cobrada"}), 409

    db.session.execute(text("""
        UPDATE appointments SET penalty_paid = TRUE WHERE id = :id
    """), {"id": appt_id})
    db.session.commit()

    return jsonify({"success": True})


# ── GET /me/analytics — métricas agregadas para dashboard ─────────────────────

@bp.get("/me/analytics")
@barber_required
def barber_analytics(barber):
    """
    Devuelve métricas agregadas del barbero autenticado para alimentar
    el dashboard de analytics (gráficos de ingresos, no-show, horarios pico, etc).

    Query params opcionales:
      - days: cantidad de días hacia atrás a considerar (default 30)
    """
    try:
        days = request.args.get("days", default=30, type=int)
        if days <= 0 or days > 365:
            days = 30

        params = {"bid": barber.id, "days": days}

        revenue_by_day = db.session.execute(text("""
            SELECT
                DATE(a.appointment_time AT TIME ZONE 'America/Argentina/Buenos_Aires') AS day,
                COUNT(*)                AS appointments,
                COALESCE(SUM(a.price), 0) AS revenue
            FROM appointments a
            WHERE a.barber_id = :bid
              AND a.status = 'completed'
              AND a.appointment_time >= NOW() - (:days || ' days')::interval
            GROUP BY day
            ORDER BY day
        """), params).mappings().all()

        status_breakdown = db.session.execute(text("""
            SELECT a.status, COUNT(*) AS count
            FROM appointments a
            WHERE a.barber_id = :bid
              AND a.status != 'available'
              AND a.appointment_time >= NOW() - (:days || ' days')::interval
            GROUP BY a.status
        """), params).mappings().all()

        hourly_load = db.session.execute(text("""
            SELECT
                EXTRACT(HOUR FROM a.appointment_time AT TIME ZONE 'America/Argentina/Buenos_Aires')::int AS hour,
                COUNT(*) AS count
            FROM appointments a
            WHERE a.barber_id = :bid
              AND a.status != 'available'
              AND a.appointment_time >= NOW() - (:days || ' days')::interval
            GROUP BY hour
            ORDER BY hour
        """), params).mappings().all()

        weekday_load = db.session.execute(text("""
            SELECT
                EXTRACT(DOW FROM a.appointment_time AT TIME ZONE 'America/Argentina/Buenos_Aires')::int AS dow,
                COUNT(*) AS count
            FROM appointments a
            WHERE a.barber_id = :bid
              AND a.status != 'available'
              AND a.appointment_time >= NOW() - (:days || ' days')::interval
            GROUP BY dow
            ORDER BY dow
        """), params).mappings().all()

        totals_row = db.session.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE a.status != 'available')               AS total_booked,
                COUNT(*) FILTER (WHERE a.status = 'completed')                AS total_completed,
                COUNT(*) FILTER (WHERE a.status = 'no_show')                  AS total_no_show,
                COUNT(*) FILTER (WHERE a.status = 'cancelled')                AS total_cancelled,
                COALESCE(SUM(a.price)        FILTER (WHERE a.status = 'completed'), 0) AS total_revenue,
                COALESCE(SUM(a.penalty_amount) FILTER (WHERE a.penalty_paid = TRUE), 0) AS total_penalties_collected
            FROM appointments a
            WHERE a.barber_id = :bid
              AND a.appointment_time >= NOW() - (:days || ' days')::interval
        """), params).mappings().first()

        total_booked = totals_row["total_booked"] or 0
        total_no_show = totals_row["total_no_show"] or 0
        no_show_rate = round((total_no_show / total_booked) * 100, 1) if total_booked else 0.0

        WEEKDAY_LABELS = ["Domingo", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"]

        return jsonify({
            "period_days": days,
            "revenue_by_day": [
                {
                    "date":         r["day"].strftime("%Y-%m-%d"),
                    "appointments": r["appointments"],
                    "revenue":      float(r["revenue"]),
                }
                for r in revenue_by_day
            ],
            "status_breakdown": [
                {"status": r["status"], "count": r["count"]}
                for r in status_breakdown
            ],
            "hourly_load": [
                {"hour": r["hour"], "count": r["count"]}
                for r in hourly_load
            ],
            "weekday_load": [
                {"weekday": WEEKDAY_LABELS[r["dow"]], "dow": r["dow"], "count": r["count"]}
                for r in weekday_load
            ],
            "totals": {
                "total_booked":              total_booked,
                "total_completed":           totals_row["total_completed"] or 0,
                "total_no_show":             total_no_show,
                "total_cancelled":           totals_row["total_cancelled"] or 0,
                "total_revenue":             float(totals_row["total_revenue"] or 0),
                "total_penalties_collected": float(totals_row["total_penalties_collected"] or 0),
                "no_show_rate":              no_show_rate,
            },
        })
    except Exception as e:
        import traceback
        logger.error("[barber_analytics] error: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": "internal", "detail": str(e)}), 500


# ── GET /export-excel ─────────────────────────────────────────────────────────

@bp.get("/export-excel")
@barber_required
def export_excel(barber):
    """Genera y retorna un .xlsx con todos los turnos del barbero autenticado."""
    from_str = request.args.get("from")
    to_str   = request.args.get("to")

    if from_str:
        try:
            from_date = datetime.strptime(from_str, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "Parámetro 'from' inválido (YYYY-MM-DD)"}), 422
    else:
        from_date = None

    if to_str:
        try:
            to_date = datetime.strptime(to_str, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "Parámetro 'to' inválido (YYYY-MM-DD)"}), 422
    else:
        to_date = None

    # Build date filter
    date_filter = ""
    params = {"bid": barber.id}
    if from_date:
        start_utc = ART.localize(datetime(from_date.year, from_date.month, from_date.day, 0, 0)).astimezone(timezone.utc)
        date_filter += " AND a.appointment_time >= :from_utc"
        params["from_utc"] = start_utc
    if to_date:
        end_utc = ART.localize(datetime(to_date.year, to_date.month, to_date.day, 23, 59, 59)).astimezone(timezone.utc)
        date_filter += " AND a.appointment_time <= :to_utc"
        params["to_utc"] = end_utc

    rows = db.session.execute(text(f"""
        SELECT
            a.appointment_time,
            a.status,
            a.service_name,
            a.price,
            a.booking_code,
            a.absence_charge_sent,
            a.absence_charge_amount,
            a.verified_at,
            COALESCE(c.full_name, u.name)        AS client_name,
            COALESCE(c.whatsapp, a.whatsapp_number) AS client_wa,
            COALESCE(c.dni, u.dni)                AS client_dni
        FROM appointments a
        LEFT JOIN clients c ON c.id = a.client_id
        LEFT JOIN users   u ON u.id = a.user_id
        WHERE a.barber_id = :bid
          AND a.status != 'available'
          {date_filter}
        ORDER BY a.appointment_time DESC
    """), params).mappings().all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Turnos"

    headers = ["Hora", "Nombre cliente", "DNI", "Teléfono", "Servicio", "Precio", "Estado", "Pago"]
    ws.append(headers)

    STATUS_LABELS = {
        "booked":      "Reservado",
        "rescheduled": "Reprogramado",
        "completed":   "Completado",
        "cancelled":   "Cancelado",
        "no_show":     "Ausente",
    }

    for r in rows:
        appt_utc = r["appointment_time"]
        if appt_utc.tzinfo is None:
            appt_utc = appt_utc.replace(tzinfo=timezone.utc)
        local_t = appt_utc.astimezone(ART)

        charge_amount = int(r["absence_charge_amount"] or 0)
        pago = f"${charge_amount:,}" if charge_amount else "—"

        ws.append([
            local_t.strftime("%H:%M"),
            r["client_name"]  or "",
            r["client_dni"]   or "",
            r["client_wa"]    or "",
            r["service_name"] or "",
            float(r["price"]) if r["price"] else 0,
            STATUS_LABELS.get(r["status"], r["status"]),
            pago,
        ])

    # Auto-width columns
    for col in ws.columns:
        max_len = max((len(str(cell.value or "")) for cell in col), default=0)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"turnos_{barber.slug}.xlsx"
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


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
