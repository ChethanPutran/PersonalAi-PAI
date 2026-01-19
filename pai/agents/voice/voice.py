import threading
import re
from queue import PriorityQueue,Empty
import speech_recognition as sr
import logging
import time
import pyttsx3 as ts
import datetime


class VoiceOutput:
    def __init__(self, queue=None,back_ground=True,debug=False):
        self.running = threading.Event()
        self.back_ground = back_ground
        self.num_items=0
        self.running.set()
        if queue is not None:
            self.queue = queue
            self.own_queue = False
        else:
            self.queue = PriorityQueue()
            self.own_queue = True

        self.engine = ts.init()
        self.engine.setProperty('volume', 1.0)
        self.engine.setProperty('voice', self.engine.getProperty('voices')[0].id)
        self.logger = logging.getLogger("VoiceOutput")

        if debug:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)

        self.wish()

    def get_logger(self):
        return self.logger

    def process_queue(self):
        while self.running.is_set():
            try:
                priority, msg = self.queue.get(block=False)
                self.get_logger().debug(f"Priority ;{priority} 🔊 Speaking: {msg}")
                self.speak(msg)
                self.get_logger().debug("Message spoken.")
            except Empty:
                self.get_logger().debug(f"No message in queue!")
                time.sleep(0.2) # No message to speak, keep looping

    def speak(self,msg):
        self.engine.say(msg)
        self.engine.runAndWait()

    def generate_chunks(self,msg,res:list=[]):
        if len(msg)>50:
            res.append(msg[:50])
            return self.generate_chunks(msg[50:],res)
        res.append(msg)
        return res
    
    def say(self,msg,priority=1):
        if self.own_queue:
            self.get_logger().debug("Adding message to speaker queue...")
            self.queue.put((priority,msg))
            # chunks = self.generate_chunks(msg)
            # for chunk in chunks:
            #     self.num_items+=1
            #     self.queue.put((priority,chunk))
        else:
            self.get_logger().debug("Error: Queue is not owed by me!")

    def wish(self):
        hour = int(datetime.datetime.now().hour)
        if hour < 12:
            self.speak("Hi Sir, Good Morning")
        elif hour < 16:
            self.speak("Hi Sir, Good Afternoon")
        elif hour < 19:
            self.speak("Hi Sir, Good Evening")
        else:
            self.speak("Hi Sir, Good Night")

    def close(self):
        self.get_logger().debug("Closing speaker...")
        self.running.clear()
        self.get_logger().debug("✅ VoiceOutput stopped cleanly.")


class VoiceInput:
    def __init__(self,debug=False):
        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = 300  # Adjust if needed
        self.recognizer.pause_threshold = 0.8   # Controls pause sensitivity
        self.queue = PriorityQueue()
        self.task_priority = {
            'record': -2,
            'stop': -5,
            'pause': -3,
            'exit': -5,
            'wait': -4
        }
        self.message_callback = None
        self.running = threading.Event()
        self.running.set()
        self.exit_callback = None
        self.logger = logging.getLogger("VoiceInput")
        if debug:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)

    def add_message_callback(self,callback):
        self.message_callback = callback

    def get_logger(self):
        return self.logger

    def get_task_priority(self, user_input):
        for task in self.task_priority:
            if re.search(r'\b' + task + r'\b', user_input, re.IGNORECASE):
                return self.task_priority[task]
        return -1

    def listen(self):
        with sr.Microphone() as source:
            self.get_logger().debug("Calibrating microphone...")
            self.recognizer.adjust_for_ambient_noise(source, duration=1)

            while self.running.is_set():
                try:
                    self.get_logger().debug("🎤 Listening... (Speak now)")
                    audio = self.recognizer.listen(source)
                    self.get_logger().debug("🧠 Recognizing...")
                    command = self.recognizer.recognize_google(audio)

                    if command.lower() == "exit":
                        self.close()
                        continue
                    priority = self.get_task_priority(command)
                    self.queue.put((priority, command))
                    self.get_logger().debug(f"✅ Queued: {command} (priority {priority})")

                except sr.UnknownValueError:
                    # self.get_logger().warning("Could not understand audio")
                    pass
                except sr.RequestError as e:
                    self.get_logger().error(f"Internet connection got disconnected: {e}")
                except Exception as e:
                    self.get_logger().error(str(e))

    def get_message(self):
        while self.running.is_set():
            try:
                if not self.queue.empty():
                    priority, msg = self.queue.get()
                    if self.message_callback is not None:
                        self.message_callback(msg,priority)
                else:
                    time.sleep(0.2)
            except Exception as e:
                self.get_logger().error(f"Error in get_message: {e}")

    def close(self, *args, **kwargs):
        if not self.running.is_set():
            return
        self.get_logger().debug("Ending listener...")
        self.running.clear()
        self.get_logger().debug("✅ VoiceInput stopped cleanly.")
        if self.exit_callback:
            self.exit_callback()

    def start(self):
        self.get_logger().debug("Starting listener...")
        self.t1 = threading.Thread(target=self.listen,daemon=True)
        self.t2 = threading.Thread(target=self.get_message,daemon=True)
        self.t1.start()
        self.t2.start()

    def add_on_exit_callback(self,callback):
        self.exit_callback = callback
  
