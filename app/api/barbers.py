from flask import Blueprint, jsonify
from sqlalchemy import text
from app.extensions import db

bp = Blueprint("barbers", __name__)

@bp.get("/shop/<shop_slug>")
def get_shop_barbers(shop_slug):
    """Devuelve todos los barberos de una barbería por su slug."""
    rows = db.session.execute(text("""
        SELECT 
            id::text, name, slug, shop_name, shop_slug,
            photo_url, instagram, specialty, bio, whatsapp
        FROM barbers
        WHERE shop_slug = :slug AND is_active = TRUE
        ORDER BY name
    """), {"slug": shop_slug}).mappings().all()

    if not rows:
        return jsonify({"error": "Barbería no encontrada"}), 404

    barbers = []
    for r in rows:
        barbers.append({
            "id":        r["id"],
            "name":      r["name"],
            "slug":      r["slug"],
            "shop_name": r["shop_name"],
            "photo_url": r["photo_url"],
            "instagram": r["instagram"],
            "specialty": r["specialty"],
            "bio":       r["bio"],
            "whatsapp":  r["whatsapp"],
        })

    return jsonify({
        "shop_name": rows[0]["shop_name"],
        "shop_slug": shop_slug,
        "barbers":   barbers,
    })


@bp.get("/<barber_slug>")
def get_barber_profile(barber_slug):
    """Devuelve el perfil de un barbero específico."""
    row = db.session.execute(text("""
        SELECT 
            id::text, name, slug, shop_name, shop_slug,
            photo_url, instagram, specialty, bio, whatsapp
        FROM barbers
        WHERE slug = :slug AND is_active = TRUE
    """), {"slug": barber_slug}).mappings().first()

    if not row:
        return jsonify({"error": "Barbero no encontrado"}), 404

    return jsonify({
        "id":        row["id"],
        "name":      row["name"],
        "slug":      row["slug"],
        "shop_name": row["shop_name"],
        "shop_slug": row["shop_slug"],
        "photo_url": row["photo_url"],
        "instagram": row["instagram"],
        "specialty": row["specialty"],
        "bio":       row["bio"],
        "whatsapp":  row["whatsapp"],
    })