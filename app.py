import os, io, logging, json, time
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
half_resolution = [dim // 2 for dim in picam2.sensor_resolution]
full_resolution = [picam2.sensor_resolution]
print(full_resolution)
main_stream = {"size": half_resolution}
video_config = picam2.create_video_configuration(main_stream)
output = None

# Set the path where the images will be stored
UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Create the upload folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Variable to track timelapse status
timelapse_running = False


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
    return render_template("home.html", title="Home", live_settings=live_settings)

    #return render_template("home.html", title="Home", live_settings=json.dumps(live_settings))

@app.route('/video_feed')
def video_feed():
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/image_gallery')
def image_gallery():
    # Specify the path to the uploads folder
    uploads_path = UPLOAD_FOLDER  # Adjust the path accordingly

    # Get a list of all image files in the uploads folder
    image_files = [f for f in os.listdir(uploads_path) if f.endswith(('.jpg', '.jpeg', '.png', '.gif'))]

    # Pass the list of image files to the template
    return render_template('image_gallery.html', image_files=image_files)

# Your existing route to update settings
@app.route('/update_live_settings', methods=['POST'])
def update_settings():
    global live_settings, picam2, video_config
    try:
        # Parse JSON data from the request
        data = request.get_json()
        # Update only the keys that are present in the data
        for key in data:
            if key in live_settings:
                if key in ('AfMode', 'AeConstraintMode', 'AeExposureMode', 'AeFlickerMode', 'AeFlickerPeriod', 'AeMeteringMode', 'AfRange', 'AfSpeed', 'AwbMode', 'ExposureTime') :
                    live_settings[key] = int(data[key])
                elif key in ('Brightness', 'Contrast', 'Saturation', 'Sharpness', 'ExposureValue', 'LensPosition'):
                    live_settings[key] = float(data[key])
                elif key in ('AeEnable', 'AwbEnable'):
                    live_settings[key] = data[key]
            elif key == "hflip":
                try:
                    video_config["transform"] = Transform(hflip=int(data[key]))
                except Exception as e:
                    logging.error(f"Error loading camera settings: {e}")
                try:
                    picam2.stop_recording()
                except Exception as e:
                    logging.error(f"Error loading camera settings: {e}")
                picam2.configure(video_config)
                start_camera_stream()

        # Update the configuration of the video feed
        configure_camera(live_settings)
        return jsonify(success=True, message="Settings updated successfully", settings=live_settings)
    except Exception as e:
        return jsonify(success=False, message=str(e))

@app.route('/update_restart_settings', methods=['POST'])
def update_restart_settings():
    global live_settings, picam2, video_config
    try:
        data = request.get_json()
        # Update settings that require a restart
        for key in data:
            if key in live_settings:
                if key in ('list_of_restart_settings'):
                    live_settings[key] = data[key]
        # Perform any additional steps for restarting the stream
        stop_camera_stream()
        start_camera_stream()
        return jsonify(success=True, message="Restart settings updated successfully", settings=live_settings)
    except Exception as e:
        return jsonify(success=False, message=str(e))

@app.route('/get_settings')
def get_settings():
    global live_settings
    try:
        return jsonify(live_settings)
    except Exception as e:
        return jsonify(error=str(e))

@app.route('/reset_default_live_settings', methods=['GET'])
def reset_default_live_settings():
    global live_settings
    try:
        default_live_settings = load_settings("live-settings.json")
        live_settings = default_live_settings
        configure_camera(live_settings)
        return jsonify(default_live_settings)
    except Exception as e:
        return jsonify(error=str(e))

# Function to take a photo and save it
def take_photo():
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{timestamp}.jpg"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    global picam2
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        image_name = f'image_{timestamp}.jpg'
        request = picam2.capture_request()
        request.save("main", filepath)
        request.release()
        logging.info(f"Image captured successfully. Path: {filepath}")
    except Exception as e:
        logging.error(f"Error capturing image: {e}")
    # Replace the following line with your actual code to capture an image
    # capture_image_and_save(filepath)

# Route to start/stop the timelapse
@app.route('/start_stop_timelapse', methods=['POST'])
def start_stop_timelapse():
    global timelapse_running

    if timelapse_running:
        # Stop the timelapse
        scheduler.remove_job('timelapse')
        timelapse_running = False
        return jsonify(status='stopped')
    else:
        # Start the timelapse
        scheduler.add_job(take_photo, trigger='interval', seconds=1, id='timelapse')
        timelapse_running = True
        return jsonify(status='started')

####################
# Start Camera Stream
####################

def start_camera_stream():
    global picam2, output, video_config
    #video_config = picam2.create_video_configuration()
    # Flip Camera #################### Make configurable
    video_config["transform"] = Transform(hflip=1, vflip=1)
    picam2.configure(video_config)
    output = StreamingOutput()
    picam2.start_recording(JpegEncoder(), FileOutput(output))
    time.sleep(1)


####################
# Configure Camera
####################

def flipcamera(live_settings):
    video_config["transform"] = Transform(hflip=1, vflip=1)

def configure_camera(live_settings):
    picam2.set_controls(live_settings)

if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)  # Change the level to DEBUG for more detailed logging

    scheduler.start()

    # Start Camera stream
    start_camera_stream()

    # Load and set camera settings
    live_settings = load_settings("live-settings.json")
    #configure_camera(live_settings)

    # Start the Flask application
    app.run(debug=False, host='0.0.0.0', port=8080)
