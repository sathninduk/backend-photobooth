import os
import sys
import uuid
import time
import threading
from datetime import datetime, timedelta

# DON'T CHANGE THIS !!!
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, send_from_directory, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_cors import CORS
from src.models.user import db
from src.routes.user import user_bp

import socket

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't matter what IP we connect to, it's just to get our own IP
        s.connect(("192.255.255.255", 1))
        IP = s.getsockname()[0]
    except:
        IP = "127.0.0.1"
    finally:
        s.close()
    return IP

app = Flask(__name__, static_folder=os.path.join(os.path.dirname(__file__), 'static'))
app.config['SECRET_KEY'] = 'asdf#FGSgvasgf$5$WGT'

# Enable CORS for all routes
CORS(app, cors_allowed_origins="*")

# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True)

app.register_blueprint(user_bp, url_prefix='/api')

# Session storage for PC-mobile pairing
sessions = {}
pc_sessions = {}  # Maps PC session ID to socket ID
mobile_sessions = {}  # Maps mobile session ID to socket ID

# Session timeout (5 minutes)
SESSION_TIMEOUT = 300

def cleanup_expired_sessions():
    """Clean up expired sessions"""
    current_time = time.time()
    expired_sessions = []
    
    for session_id, session_data in sessions.items():
        if current_time - session_data['created_at'] > SESSION_TIMEOUT:
            expired_sessions.append(session_id)
    
    for session_id in expired_sessions:
        if session_id in sessions:
            del sessions[session_id]
        if session_id in pc_sessions:
            del pc_sessions[session_id]
        if session_id in mobile_sessions:
            del mobile_sessions[session_id]

def start_cleanup_timer():
    """Start periodic cleanup of expired sessions"""
    cleanup_expired_sessions()
    timer = threading.Timer(60.0, start_cleanup_timer)  # Run every minute
    timer.daemon = True
    timer.start()

# Start cleanup timer
start_cleanup_timer()

@app.route('/api/generate-session', methods=['POST'])
def generate_session():
    """Generate a new session for PC"""
    session_id = str(uuid.uuid4())
    sessions[session_id] = {
        'id': session_id,
        'created_at': time.time(),
        'pc_connected': False,
        'mobile_connected': False,
        'status': 'idle'  # idle, connected, capturing
    }
    
    return jsonify({
        'session_id': session_id,
        'qr_data': f"http://{get_local_ip()}:5000/mobile/{session_id}",
        'status': 'success'
    })

@app.route('/api/session/<session_id>/status', methods=['GET'])
def get_session_status(session_id):
    """Get session status"""
    if session_id not in sessions:
        return jsonify({'error': 'Session not found'}), 404
    
    return jsonify({
        'session': sessions[session_id],
        'status': 'success'
    })

@socketio.on('connect')
def handle_connect():
    print(f'Client connected: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    print(f'Client disconnected: {request.sid}')
    
    # Clean up session mappings
    session_to_remove = None
    for session_id, socket_id in pc_sessions.items():
        if socket_id == request.sid:
            session_to_remove = session_id
            break
    
    if session_to_remove:
        del pc_sessions[session_to_remove]
        if session_to_remove in sessions:
            sessions[session_to_remove]['pc_connected'] = False
            sessions[session_to_remove]['status'] = 'idle'
    
    # Check mobile sessions
    session_to_remove = None
    for session_id, socket_id in mobile_sessions.items():
        if socket_id == request.sid:
            session_to_remove = session_id
            break
    
    if session_to_remove:
        del mobile_sessions[session_to_remove]
        if session_to_remove in sessions:
            sessions[session_to_remove]['mobile_connected'] = False
            # Notify PC about mobile disconnect
            if session_to_remove in pc_sessions:
                socketio.emit('mobile_disconnected', room=pc_sessions[session_to_remove])

@socketio.on('join_pc_session')
def handle_join_pc_session(data):
    """PC joins a session"""
    session_id = data.get('session_id')
    
    if session_id not in sessions:
        emit('error', {'message': 'Session not found'})
        return
    
    join_room(session_id)
    pc_sessions[session_id] = request.sid
    sessions[session_id]['pc_connected'] = True
    
    emit('pc_joined', {'session_id': session_id})
    print(f'PC joined session: {session_id}')

@socketio.on('join_mobile_session')
def handle_join_mobile_session(data):
    """Mobile joins a session"""
    session_id = data.get('session_id')
    
    if session_id not in sessions:
        emit('error', {'message': 'Session not found'})
        return
    
    join_room(session_id)
    mobile_sessions[session_id] = request.sid
    sessions[session_id]['mobile_connected'] = True
    sessions[session_id]['status'] = 'connected'
    
    # Notify PC about mobile connection
    if session_id in pc_sessions:
        socketio.emit('mobile_connected', {'session_id': session_id}, room=pc_sessions[session_id])
    
    emit('mobile_joined', {'session_id': session_id})
    print(f'Mobile joined session: {session_id}')

@socketio.on('capture_request')
def handle_capture_request(data):
    """Mobile requests image capture"""
    session_id = data.get('session_id')
    
    if session_id not in sessions:
        emit('error', {'message': 'Session not found'})
        return
    
    if session_id not in pc_sessions:
        emit('error', {'message': 'PC not connected'})
        return
    
    sessions[session_id]['status'] = 'capturing'
    
    # Send capture request to PC
    socketio.emit('capture_image', {'session_id': session_id}, room=pc_sessions[session_id])
    print(f'Capture requested for session: {session_id}')

@socketio.on('image_captured')
def handle_image_captured(data):
    """PC sends captured image"""
    session_id = data.get('session_id')
    image_data = data.get('image_data')
    
    if session_id not in sessions:
        emit('error', {'message': 'Session not found'})
        return
    
    if session_id not in mobile_sessions:
        emit('error', {'message': 'Mobile not connected'})
        return
    
    sessions[session_id]['status'] = 'connected'
    
    # Send image to mobile
    socketio.emit('image_received', {
        'session_id': session_id,
        'image_data': image_data
    }, room=mobile_sessions[session_id])
    
    print(f'Image sent to mobile for session: {session_id}')

@socketio.on('end_session')
def handle_end_session(data):
    """End the current session"""
    session_id = data.get('session_id')
    
    if session_id not in sessions:
        emit('error', {'message': 'Session not found'})
        return
    
    # Notify both PC and mobile
    if session_id in pc_sessions:
        socketio.emit('session_ended', {'session_id': session_id}, room=pc_sessions[session_id])
    
    if session_id in mobile_sessions:
        socketio.emit('session_ended', {'session_id': session_id}, room=mobile_sessions[session_id])
    
    # Clean up session
    if session_id in sessions:
        del sessions[session_id]
    if session_id in pc_sessions:
        del pc_sessions[session_id]
    if session_id in mobile_sessions:
        del mobile_sessions[session_id]
    
    print(f'Session ended: {session_id}')

# Relay webcam error from desktop to mobile
def handle_webcam_error(data):
    session_id = data.get('session_id')
    message = data.get('message', 'Webcam error')
    if session_id in mobile_sessions:
        socketio.emit('webcam_error', {
            'session_id': session_id,
            'message': message
        }, room=mobile_sessions[session_id])
    print(f'Webcam error relayed to mobile for session: {session_id}')
socketio.on_event('webcam_error', handle_webcam_error)

# uncomment if you need to use database
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(os.path.dirname(__file__), 'database', 'app.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)
with app.app_context():
    db.create_all()

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    static_folder_path = app.static_folder
    if static_folder_path is None:
            return "Static folder not configured", 404

    if path != "" and os.path.exists(os.path.join(static_folder_path, path)):
        return send_from_directory(static_folder_path, path)
    else:
        index_path = os.path.join(static_folder_path, 'index.html')
        if os.path.exists(index_path):
            return send_from_directory(static_folder_path, 'index.html')
        else:
            return "index.html not found", 404

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)

