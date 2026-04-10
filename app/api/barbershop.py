import re

from flask import Blueprint, jsonify, request
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Barber, Shop
from app.api.dashboard import _make_barber_token

bp = Blueprint("barbershop", __name__)


@bp.post("/register")
def register():
    data        = request.get_json() or {}
    shop_name   = (data.get("shop_name")   or "").strip()
    owner_name  = (data.get("owner_name")  or "").strip()
    owner_email = (data.get("owner_email") or "").strip().lower()
    password    = (data.get("password")    or "").strip()
    whatsapp    = (data.get("whatsapp")    or "").strip()

    if not shop_name or not owner_name or not owner_email or not password:
        return jsonify({"error": "Nombre de barbería, nombre del dueño, email y contraseña son obligatorios"}), 422

    # ── Slug único para la barbería ────────────────────────────────────────────
    slug_base = re.sub(r"[^a-z0-9]+", "-", shop_name.lower()).strip("-")
    slug, counter = slug_base, 1
    while Shop.query.filter_by(slug=slug).first():
        slug = f"{slug_base}-{counter}"
        counter += 1

    # ── Slug único para el barbero dueño ──────────────────────────────────────
    barber_slug_base = re.sub(r"[^a-z0-9]+", "-", owner_name.lower()).strip("-")
    barber_slug, bc = barber_slug_base, 1
    while Barber.query.filter_by(slug=barber_slug).first():
        barber_slug = f"{barber_slug_base}-{bc}"
        bc += 1

    # ── Crear Shop ─────────────────────────────────────────────────────────────
    shop = Shop(
        slug                = slug,
        name                = shop_name,
        whatsapp            = whatsapp,
        owner_email         = owner_email,
        plan                = "solo",
        flash_promo_active  = False,
        admin_password_hash = generate_password_hash(password),
    )
    db.session.add(shop)
    db.session.flush()  # obtener shop.id antes de crear el barbero

    # ── Crear Barber (dueño) ───────────────────────────────────────────────────
    barber = Barber(
        shop_id       = shop.id,
        name          = owner_name,
        slug          = barber_slug,
        shop_name     = shop_name,
        shop_slug     = slug,
        whatsapp      = whatsapp,
        is_active     = True,
        password_hash = generate_password_hash(password),
    )
    db.session.add(barber)
    db.session.commit()

    token = _make_barber_token(str(barber.id))
    return jsonify({
        "token":  token,
        "barber": barber.to_dict(),
        "shop":   shop.to_dict(),
    }), 201
