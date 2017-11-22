from flask import Flask, Response, request, abort
import uuid
import json
import ra
import cv2
import skvideo.io
import os.path
import pkg_resources

app = Flask(__name__, static_folder=None)

class Tracker(object):
    def __init__(self, video, frame, labels):
        self.tracker = cv2.MultiTracker_create()
        self.video_accessor = video_store(video_path(video))
        self.frame = frame
        self.labels = labels
        self.initialized = False
        print "Starting new tracker for video %s based on keyframe %s" % (video, frame)

    def __iter__(self):
        return self
        
    def next(self):
        image = self.video_accessor[self.frame]

        if not self.initialized:
            for label in self.labels:
                if not self.tracker.add(cv2.TrackerMIL_create(), image, tuple(label['bbox'])):
                    raise Exception("Unable to add tracker bbox")
            self.initialized = True
                
        ok, boxes = self.tracker.update(image)
        self.frame += 1
        if not ok:
            raise Exception("Unable to update tracker with current frame")

        res = []
        for bbox, label in zip(boxes.tolist(), self.labels):
            label = dict(label)
            label['bbox'] = bbox
            res.append(label)

        return res

class TrackerCache(object):
    def __init__(self, video, frame, bboxes):
        self.video = video
        self.frame = frame
        self.bboxes = bboxes
        self.key = json.dumps(self.bboxes, sort_keys=True)
        self.basepath = os.path.join('upload', 'tracker', self.video, str(self.frame), self.key)

    def frame_path(self, frame):
        return os.path.join(self.basepath, "%s.json" % frame)
        
    def __contains__(self, frame):
        path = self.frame_path(frame)
        exists = os.path.exists(path)
        if not exists:
            print "Cache miss for frame %s (%s)" % (frame, path)
        return os.path.exists(self.frame_path(frame))
    
    def __getitem__(self, frame):
        with open(self.frame_path(frame)) as f:
            return json.load(f)
    
    def __setitem__(self, frame, bboxes):
        path = self.frame_path(frame)
        ensuredirs(os.path.split(path)[0])
        with open(path, "w") as f:
            json.dump(bboxes, f)
    
video_store = ra.Store(skvideo.io.vreader)
tracker_store = ra.Store(Tracker, TrackerCache)


def ensuredirs(pth):
    if os.path.exists(pth):
        return
    os.makedirs(pth)


def video_path(id):
    assert '/' not in id
    return os.path.join('upload', 'video', id.encode('utf-8'))

def session_path(videoid, sessionid):
    assert '/' not in videoid
    assert '/' not in sessionid
    return os.path.join('upload', 'session', ("%s-%s" % (videoid, sessionid)).encode('utf-8'))

ensuredirs("upload/video")
ensuredirs("upload/tracker")
ensuredirs("upload/session")


@app.route('/video', methods=['PUT', 'POST'])
def upload():
    res = {}
    if 'file' in request.files:
        file = request.files['file']
        ext = os.path.splitext(file.filename)[-1]
        assert "/" not in ext
        if file.filename != '':
            res['id'] = str(uuid.uuid4()) + ext
            file.save(video_path(res['id']))
    return Response(json.dumps(res), mimetype='text/json')
            
@app.route('/video/<video>/image/<frame>', methods=['GET'])
def get_frame_image(video, frame):
    frame_content = video_store(video_path(video))[int(frame)]
    retval, frame_img = cv2.imencode(".png", frame_content)
    return Response(frame_img.tobytes(), mimetype='image/png')

@app.route('/video/<video>/session/<session>/metadata', methods=['GET'])
def get_metadata(video, session):
    metadata = skvideo.io.ffprobe(video_path(video))
    metadata['keyframes'] = []

    session = session_path(video, session)
    if os.path.exists(session):
        with open(session) as f:
            metadata['keyframes'] = [int(key) for key in json.load(f)['keyframes'].iterkeys()]

    return Response(json.dumps(metadata), mimetype='text/json')

@app.route('/video/<video>/session/<session>/bboxes/<frame>', methods=['GET'])
def get_frame_bboxes(video, session, frame):
    assert '/' not in video
    assert '/' not in session
    
    res = {"labels": [], 'keyframe': -1}

    session = session_path(video, session)
    if os.path.exists(session):
        with open(session) as f:
            data = json.load(f)

        frame = int(frame)

        keyframes = sorted(keyframe
                           for keyframe in (int(key)
                                            for key in data['keyframes'].iterkeys())
                           if keyframe <= frame)

        if keyframes:
            res['keyframe'] = keyframe = keyframes[-1]

            res['labels'] = tracker_store(video, keyframe, data['keyframes'][str(keyframe)]['labels'])[frame]

    return Response(json.dumps(res), mimetype='text/json')

@app.route('/video/<video>/session/<session>/bboxes/<frame>', methods=['POST'])
def set_frame_bboxes(video, session, frame):
    session = session_path(video, session)
    data = {'keyframes': {}}
    if os.path.exists(session):
        with open(session) as f:
            data = json.load(f)

    frame_data = request.get_json()

    if not frame_data.get("labels"):
        if frame in data['keyframes']:
            del data['keyframes'][frame]
    else:
        data['keyframes'][frame] = frame_data
    
    with open(session, "w") as f:
        json.dump(data, f)

    return Response(json.dumps({}), mimetype='text/json')

@app.route('/')
@app.route('/<path:path>')
def get_resource(path = ''):  # pragma: no cover
    mimetypes = {
        ".css": "text/css",
        ".html": "text/html",
        ".js": "application/javascript",
    }
    ext = os.path.splitext(path)[1]
    mimetype = mimetypes.get(ext, "text/html")
    try:
        content = pkg_resources.resource_string('labelv', os.path.join("static", path))
    except IOError:
        try:
            content = pkg_resources.resource_string('labelv', os.path.join("static", path, "index.html"))
        except IOError:
            abort(404)
    return Response(content, mimetype=mimetype)
        
def main():
    app.run(host='localhost', port=4711)
