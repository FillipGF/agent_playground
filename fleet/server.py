# -*- coding: utf-8 -*-
import os
import sys
import json
import logging
from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy

# Configure logging
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Load database config
base_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.abspath(os.path.join(base_dir, "..", "config.json"))

if not os.path.exists(config_path):
    logger.error(f"Configuration file not found: {config_path}")
    sys.exit(1)

with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)

db_config = config.get("mysql", {})
if not db_config:
    logger.error("MySQL connection settings not found in config.json")
    sys.exit(1)

# Connection string using PyMySQL
db_uri = f"mysql+pymysql://{db_config['user']}:{db_config['password']}@{db_config['host']}/{db_config['database']}?charset=utf8mb4"
app.config['SQLALCHEMY_DATABASE_URI'] = db_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Models
class DevPlanTask(db.Model):
    __tablename__ = 'dev_plan_tasks'
    id = db.Column(db.String(100), primary_key=True)
    city = db.Column(db.String(100), nullable=False)
    text = db.Column(db.Text, nullable=False)
    completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.String(50))
    created_raw = db.Column(db.String(50))
    completed_at = db.Column(db.String(50))

class DevPlanKanban(db.Model):
    __tablename__ = 'dev_plan_kanban'
    id = db.Column(db.String(100), primary_key=True)
    city = db.Column(db.String(100), nullable=False)
    text = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50), nullable=False)

class StationDecision(db.Model):
    __tablename__ = 'dev_plan_station_decisions'
    vending_id = db.Column(db.String(50), primary_key=True)
    status = db.Column(db.String(50), nullable=False)

# Ensure tables are created
with app.app_context():
    try:
        db.create_all()
        logger.info("Database tables verified/created successfully.")
    except Exception as e:
        logger.error(f"Error creating database tables: {e}")

# API Routes

@app.route('/')
def index():
    # Serve the compiled HTML file from the parent directory
    parent_dir = os.path.abspath(os.path.join(base_dir, ".."))
    return send_from_directory(parent_dir, 'План развития городов_Июнь.html')

# Tasks endpoints
@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    city = request.args.get('city', 'Общее')
    tasks = DevPlanTask.query.filter_by(city=city).all()
    return jsonify([{
        'id': t.id,
        'city': t.city,
        'text': t.text,
        'completed': t.completed,
        'created_at': t.created_at,
        'created_raw': t.created_raw,
        'completed_at': t.completed_at
    } for t in tasks])

@app.route('/api/tasks', methods=['POST'])
def save_task():
    data = request.json
    if not data or 'id' not in data:
        return jsonify({'error': 'Invalid data'}), 400
        
    task = DevPlanTask.query.get(data['id'])
    if not task:
        task = DevPlanTask(id=data['id'])
        db.session.add(task)
        
    task.city = data.get('city', 'Общее')
    task.text = data.get('text', '')
    task.completed = bool(data.get('completed', False))
    task.created_at = data.get('created_at', '')
    task.created_raw = data.get('created_raw', '')
    task.completed_at = data.get('completed_at', None)
    
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/tasks/<task_id>', methods=['DELETE'])
def delete_task(task_id):
    task = DevPlanTask.query.get(task_id)
    if task:
        db.session.delete(task)
        db.session.commit()
    return jsonify({'success': True})

# Kanban endpoints
@app.route('/api/kanban', methods=['GET'])
def get_kanban():
    city = request.args.get('city', 'Общее')
    cards = DevPlanKanban.query.filter_by(city=city).all()
    return jsonify([{
        'id': c.id,
        'city': c.city,
        'text': c.text,
        'status': c.status
    } for c in cards])

@app.route('/api/kanban', methods=['POST'])
def save_kanban():
    data = request.json
    if not data or 'id' not in data:
        return jsonify({'error': 'Invalid data'}), 400
        
    card = DevPlanKanban.query.get(data['id'])
    if not card:
        card = DevPlanKanban(id=data['id'])
        db.session.add(card)
        
    card.city = data.get('city', 'Общее')
    card.text = data.get('text', '')
    card.status = data.get('status', 'potential')
    
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/kanban/<card_id>', methods=['DELETE'])
def delete_kanban(card_id):
    card = DevPlanKanban.query.get(card_id)
    if card:
        db.session.delete(card)
        db.session.commit()
    return jsonify({'success': True})

# Station Decisions endpoints
@app.route('/api/decisions', methods=['GET'])
def get_decisions():
    decisions = StationDecision.query.all()
    return jsonify([{
        'vending_id': d.vending_id,
        'status': d.status
    } for d in decisions])

@app.route('/api/decisions', methods=['POST'])
def save_decision():
    data = request.json
    if not data or 'vending_id' not in data:
        return jsonify({'error': 'Invalid data'}), 400
        
    dec = StationDecision.query.get(data['vending_id'])
    if not dec:
        dec = StationDecision(vending_id=data['vending_id'])
        db.session.add(dec)
        
    dec.status = data.get('status', 'none')
    db.session.commit()
    return jsonify({'success': True})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
