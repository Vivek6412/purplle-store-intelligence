import cv2
import os

os.makedirs('temp_frames', exist_ok=True)
videos = [
    ('data/clips/Store-1/CAM 1 - zone.mp4', 'temp_frames/store1_cam1.jpg'),
    ('data/clips/Store-1/CAM 2 - zone.mp4', 'temp_frames/store1_cam2.jpg'),
    ('data/clips/Store-1/CAM 3 - entry.mp4', 'temp_frames/store1_cam3.jpg'),
    ('data/clips/Store-1/CAM 5 - billing.mp4', 'temp_frames/store1_cam5.jpg'),
    ('data/clips/Store-2/billing_area.mp4', 'temp_frames/store2_billing.jpg'),
    ('data/clips/Store-2/entry 1.mp4', 'temp_frames/store2_entry1.jpg'),
    ('data/clips/Store-2/entry 2.mp4', 'temp_frames/store2_entry2.jpg'),
    ('data/clips/Store-2/zone.mp4', 'temp_frames/store2_zone.jpg')
]

for vid, out in videos:
    cap = cv2.VideoCapture(vid)
    ret, frame = cap.read()
    if ret:
        cv2.imwrite(out, frame)
    cap.release()
print("Done")
