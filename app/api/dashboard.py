from flask import Blueprint, request, jsonify
from app.extensions import db 
from sqlalchemy import text # Importante para consultas SQL puras

bp = Blueprint('dashboard', __name__)

@bp.route('/dashboard', methods=['GET'])
def get_dashboard():
    barber_id = request.args.get('barber_id')
    
    if not barber_id:
        return jsonify({"error": "Falta barber_id"}), 400

    try:
        # En SQLAlchemy se usa db.session.execute con text()
        query = text("""
            SELECT c.full_name, a.service_name, a.appointment_time, a.price 
            FROM appointments a
            JOIN clients c ON a.client_id = c.id
            WHERE a.barber_id = :barber_id
            ORDER BY a.appointment_time DESC
        """)
        
        result = db.session.execute(query, {"barber_id": barber_id})
        rows = result.fetchall()
        
        reservas = []
        for row in rows:
            # SQLAlchemy devuelve objetos que se acceden por índice o nombre
            reservas.append({
                "nombre": row[0],
                "service_name": row[1],
                "fecha": row[2].strftime('%d/%m/%Y') if row[2] else "Sin fecha",
                "hora": row[2].strftime('%H:%M') if row[2] else "Sin hora",
                "price": float(row[3]) if row[3] else 0.0
            })
            
        return jsonify({"reservations": reservas}), 200
        
    except Exception as e:
        print(f"❌ Error en SQLAlchemy: {e}")
        return jsonify({"error": str(e)}), 500