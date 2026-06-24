# NVR AI Platform

This project provides a skeleton for a real-time NVR intelligent analysis platform with:

- RTSP real-time stream capture
- Green-screen / vertical green line detection
- YOLOv8 object detection with false positive filtering
- CLIP + FAISS based text-to-video-frame retrieval
- PyQt GUI for preview, search and alerts

See `run.py` to start the GUI. Configure `config/settings.py` for paths and parameters.
