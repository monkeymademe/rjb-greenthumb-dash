import os, io, logging, json
from datetime import datetime
from threading import Condition

from flask import Flask, render_template, request, jsonify, Response
from apscheduler.schedulers.background import BackgroundScheduler

from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput
from libcamera import Transform, controls

# Int Flask
app = Flask(__name__)
scheduler = BackgroundScheduler()

# Int Picamera2 and default settings
picam2 = Picamera2()
output = None

class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()

def generate():
    global output
    while True:
        with output.condition:
            output.condition.wait()
            frame = output.frame
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

# Settings Options
af_state_list = ["Manual", "Auto", "Continuous"]

# Load camera settings from config file
def load_settings(settings_file):
    try:
        with open(settings_file, 'r') as file:
            settings = json.load(file)
            return settings
    except FileNotFoundError:
        # Return default settings if the file is not found
        logging.error(f"Settings file {settings_file} not found")
        return None
    except Exception as e:
        logging.error(f"Error loading camera settings: {e}")
        return None

####################
# Site Routes
####################
@app.route("/")
def home():
    return render_template("home.html", title="Home", camera_settings=camera_settings)

    #return render_template("home.html", title="Home", camera_settings=json.dumps(camera_settings))

@app.route('/video_feed')
def video_feed():
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

# Your existing route to update settings
@app.route('/update_settings', methods=['POST'])
def update_settings():
    global camera_settings
    try:
        # Parse JSON data from the request
        data = request.get_json()
        print(data)
        # Update only the keys that are present in the data
        for key in data:
            if key in camera_settings:
                if key in ('AfMode', 'AeConstraintMode') :
                    camera_settings[key] = int(data[key])
                elif key == 'Brightness':
                    camera_settings[key] = float(data[key])
                elif key == 'AeEnable':
                    camera_settings[key] = data[key]
        # Update the configuration of the video feed
        configure_camera(camera_settings)
        return jsonify(success=True, message="Settings updated successfully", settings=camera_settings)
    except Exception as e:
        return jsonify(success=False, message=str(e))

@app.route('/get_settings')
def get_settings():
    global camera_settings
    try:
        return jsonify(camera_settings)
    except Exception as e:
        return jsonify(error=str(e))

####################
# Start Camera Stream
####################

def start_camera_stream():
    global picam2, output
    video_config = picam2.create_video_configuration()
    # Flip Camera #################### Make configurable
    video_config["transform"] = Transform(hflip=1, vflip=1)
    picam2.configure(video_config)
    output = StreamingOutput()
    picam2.start_recording(JpegEncoder(), FileOutput(output))

####################
# Configure Camera
####################

def configure_camera(camera_settings):
    print(camera_settings)
    picam2.set_controls(camera_settings)

if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)  # Change the level to DEBUG for more detailed logging

    # Start Camera stream
    start_camera_stream()

    # Load and set camera settings
    camera_settings = load_settings("config.json")
    configure_camera(camera_settings)

    # Start the Flask application
    app.run(debug=False, host='0.0.0.0', port=8080)
