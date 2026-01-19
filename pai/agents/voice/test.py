import threading
import time
import signal
import sys

def worker():
    while True:
        print("Worker thread is running...")
        time.sleep(1)

def signal_handler(sig, frame):
    print("\nCtrl+C received! Shutting down...")
    # Perform any necessary cleanup here
    sys.exit(0)

def main():
    th = threading.Thread(target=worker)
    th.daemon = True  # Allow the main thread to exit even if this thread is running
    th.start()

    # signal.signal(signal.SIGINT, signal_handler)
    print("Main thread started. Press Ctrl+C to exit.")
    while True:
        time.sleep(2)
        print("Main thread is still alive...")

if __name__ == "__main__":
    main()