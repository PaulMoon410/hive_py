import os
import sys
import threading
import time
from flask import Flask, Response, render_template_string

import dashboard  # Assumes dashboard.py has a main() entrypoint

LOG_FILE = "bot_output.log"

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Bot Terminal</title>
    <style>
        body { background: #111; color: #eee; font-family: monospace; }
        #terminal { white-space: pre-wrap; background: #222; padding: 1em; border-radius: 8px; }
    </style>
</head>
<body>
    <h2>Bot Output Terminal</h2>
    <div id="terminal"></div>
    <script>
        var term = document.getElementById('terminal');
        var es = new EventSource('/stream');
        es.onmessage = function(e) {
            term.textContent += e.data + '\n';
            window.scrollTo(0, document.body.scrollHeight);
        };
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/stream")
def stream():
    def event_stream():
        with open(LOG_FILE, "a+") as f:
            f.seek(0, os.SEEK_END)
            while True:
                line = f.readline()
                if line:
                    yield f"data: {line.rstrip()}\n\n"
                else:
                    time.sleep(0.5)
    return Response(event_stream(), mimetype="text/event-stream")

def run_dashboard():
    # Redirect stdout/stderr to log file
    with open(LOG_FILE, "a") as f:
        sys.stdout = f
        sys.stderr = f
        dashboard.main()

if __name__ == "__main__":
    # Start dashboard in a background thread
    threading.Thread(target=run_dashboard, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True)
