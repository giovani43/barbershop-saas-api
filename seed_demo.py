#!/usr/bin/env python3
"""
Seed data: MVZ Barberia — 3 barbers, 2 services,
and appointment slots for the next 7 days (Mon–Sat, 09:00–20:00).

Usage:
    python seed_demo.py

Admin login after seeding:
    shop_slug: mvzbarberia
    password:  admin123
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timezone, timedelta

import pytz
from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import Appointment, Barber, Client, Service, Shop

ART  = pytz.timezone("America/Argentina/Buenos_Aires")
SLUG = "mvzbarberia"


def seed():
    app = create_app()
    with app.app_context():

        # ── Remove old BarberPro data if present ───────────────────────────────
        old_shop = Shop.query.filter_by(slug="barberpro").first()
        if old_shop:
            for barber in Barber.query.filter_by(shop_id=old_shop.id).all():
                Appointment.query.filter_by(barber_id=barber.id).delete()
                Client.query.filter_by(barber_id=barber.id).delete()
            Barber.query.filter_by(shop_id=old_shop.id).delete()
            Service.query.filter_by(shop_id=old_shop.id).delete()
            db.session.delete(old_shop)
            db.session.commit()
            print("Removed old BarberPro data.")

        existing_shop = Shop.query.filter_by(slug=SLUG).first()
        if existing_shop:
            for barber in Barber.query.filter_by(shop_id=existing_shop.id).all():
                Appointment.query.filter_by(barber_id=barber.id).delete()
                Client.query.filter_by(barber_id=barber.id).delete()
            Barber.query.filter_by(shop_id=existing_shop.id).delete()
            Service.query.filter_by(shop_id=existing_shop.id).delete()
            db.session.delete(existing_shop)
            db.session.commit()
            print("Removed existing MVZ Barberia data (re-seeding).")

        # ── Shop ──────────────────────────────────────────────────────────────
        shop = Shop(
            slug                = SLUG,
            name                = "MVZ Barberia",
            address             = "Humboldt 689, CABA, Buenos Aires",
            whatsapp            = "+5491164206213",
            plan                = "shop",
            flash_promo_active  = False,
            admin_password_hash = generate_password_hash("admin123"),
        )
        db.session.add(shop)
        db.session.flush()

        # ── Services ──────────────────────────────────────────────────────────
        services = [
            Service(shop_id=shop.id, name="Corte",                 price=15000, duration_minutes=30, display_order=0),
            Service(shop_id=shop.id, name="Corte + cejas + barba", price=20000, duration_minutes=45, display_order=1),
        ]
        for s in services:
            db.session.add(s)

        # ── Barbers ───────────────────────────────────────────────────────────
        barbers_data = [
            {
                "name":      "Ezequiel",
                "slug":      "ezequiel-nonino",
                "specialty": "Corte & Estilo",
                "instagram": "@ezequiel.nonino",
                "photo_url": "/barbers/ezequiel.jpg",
                "whatsapp":  "+5491164206213",
            },
            {
                "name":      "Nico",
                "slug":      "nico-alexander",
                "specialty": "Corte & Barba",
                "instagram": "@alexander_nicolasi",
                "photo_url": "/barbers/nico.jpg",
                "whatsapp":  "+5491164206213",
            },
            {
                "name":      "Braian",
                "slug":      "braian-resquin",
                "specialty": "Fade & Diseño",
                "instagram": "@braianresquin_",
                "photo_url": "/barbers/braian.jpg",
                "whatsapp":  "+5491164206213",
            },
        ]
        barber_objs = []
        for bd in barbers_data:
            existing = Barber.query.filter_by(slug=bd["slug"]).first()
            if existing:
                existing.shop_id   = shop.id
                existing.shop_name = "MVZ Barberia"
                existing.shop_slug = SLUG
                existing.specialty = bd["specialty"]
                existing.whatsapp  = bd["whatsapp"]
                existing.instagram = bd.get("instagram")
                existing.photo_url = bd.get("photo_url")
                existing.is_active = True
                barber_objs.append(existing)
            else:
                b = Barber(
                    shop_id   = shop.id,
                    name      = bd["name"],
                    slug      = bd["slug"],
                    shop_name = "MVZ Barberia",
                    shop_slug = SLUG,
                    specialty = bd["specialty"],
                    whatsapp  = bd["whatsapp"],
                    instagram = bd.get("instagram"),
                    photo_url = bd.get("photo_url"),
                    is_active = True,
                )
                db.session.add(b)
                barber_objs.append(b)

        db.session.flush()

        # ── Slots: next 7 days, Mon–Sat, 09:00–20:00, every 30 min ───────────
        today = datetime.now(ART).date()
        total = 0

        for barber in barber_objs:
            for day_offset in range(7):
                target = today + timedelta(days=day_offset)
                if target.weekday() == 6:   # skip Sunday (6)
                    continue
                h, m = 9, 0
                while True:
                    local_dt = ART.localize(datetime(target.year, target.month, target.day, h, m))
                    utc_dt   = local_dt.astimezone(timezone.utc)

                    slot = Appointment(
                        barber_id        = barber.id,
                        appointment_time = utc_dt,
                        status           = "available",
                        service_name     = "Corte",
                        price            = 15000,
                        duration_minutes = 30,
                    )
                    db.session.add(slot)
                    total += 1

                    m += 30
                    if m >= 60:
                        m -= 60
                        h += 1
                    if h >= 20:
                        break

        db.session.commit()

        print("OK MVZ Barberia seeded!")
        print(f"  Shop ID   : {shop.id}")
        print(f"  Barbers   : {', '.join(b.name for b in barber_objs)}")
        print(f"  Services  : Corte $15.000 · Corte+cejas+barba $20.000")
        print(f"  Slots     : {total} (3 barberos × días Lun–Sáb × 22 slots/día)")
        print()
        print(f"  Admin login: slug={SLUG}  |  password=admin123")
        print(f"  Client URL: /shop/{SLUG}")


if __name__ == "__main__":
    seed()
