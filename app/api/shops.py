from flask import Blueprint, jsonify
from sqlalchemy import func
from datetime import datetime, timezone
import pytz

from app.extensions import db
from app.models import Shop, Barber, Service, Appointment

bp = Blueprint("shops", __name__)
ART = pytz.timezone("America/Argentina/Buenos_Aires")


@bp.get("/<shop_slug>")
def get_shop(shop_slug):
    """Public endpoint: shop info + barbers + services + today's availability."""
    shop = Shop.query.filter_by(slug=shop_slug).first()
    if not shop:
        return jsonify({"error": "Barbería no encontrada"}), 404

    barbers  = (Barber.query
                .filter_by(shop_id=shop.id, is_active=True)
                .order_by(Barber.name)
                .all())
    services = (Service.query
                .filter_by(shop_id=shop.id, is_active=True)
                .order_by(Service.display_order)
                .all())

    # Count available slots today per barber
    today     = datetime.now(ART).date()
    start_utc = ART.localize(datetime(today.year, today.month, today.day, 0, 0)).astimezone(timezone.utc)
    end_utc   = ART.localize(datetime(today.year, today.month, today.day, 23, 59)).astimezone(timezone.utc)

    availability = {}
    if barbers:
        barber_ids = [b.id for b in barbers]
        rows = (db.session.query(
                    Appointment.barber_id,
                    func.count(Appointment.id).label("cnt"),
                )
                .filter(
                    Appointment.barber_id.in_(barber_ids),
                    Appointment.appointment_time.between(start_utc, end_utc),
                    Appointment.status == "available",
                )
                .group_by(Appointment.barber_id)
                .all())
        for barber_id, cnt in rows:
            availability[str(barber_id)] = int(cnt)

    barbers_data = []
    for b in barbers:
        d = b.to_dict()
        d["available_today"] = availability.get(str(b.id), 0)
        barbers_data.append(d)

    return jsonify({
        "shop":     shop.to_dict(),
        "barbers":  barbers_data,
        "services": [s.to_dict() for s in services],
    })
