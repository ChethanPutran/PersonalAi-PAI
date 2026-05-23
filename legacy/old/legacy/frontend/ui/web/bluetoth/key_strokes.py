# import keyboard
# import sounddevice as sd
# from scipy.io.wavfile import write
# from voice.voice import VoiceInput, VoiceOutput


# def get_command():

#     fs = 16000  # Sampling frequency
#     duration = 5  # seconds

#     print("Recording...")
#     recording = sd.rec(int(duration * fs), samplerate=fs, channels=1)
#     sd.wait()
#     print("Done.")

#     write("output.wav", fs, recording)


# HANDLE_COMMAND = "down"
# voice_input  = VoiceInput()
# voice_output = VoiceOutput()


# voice_input.add_message_callback(voice_output.say)


# def on_event(e):
#     print(f"Key: {e.name} | Event: {e.event_type}")
#     if e.name == HANDLE_COMMAND:
#         print("Asking the assistance...")
#         voice_output.say("Hello sir what can I do for you?")
#         voice_input.listen()
        
    
# def close():
#     voice_output.close()
#     voice_input.close()
#     exit(0)

# try:
#     keyboard.hook(on_event)
#     keyboard.wait()
# except KeyboardInterrupt:
#     close
# # while True:
# #     event = keyboard.read_event()
# #     event = str(event)
# #     print(event)
import asyncio
# # from bluetooth import *
from bleak import BleakScanner,BleakClient
from bleak.exc import BleakDeviceNotFoundError
    
async def scan_devices():
    bs =  BleakScanner()
    # device,data = await bs.advertisement_data()
    # print(device,data)
    devices = await bs.find_device_by_name("Noise Buds N1 Pro")
    print("Devices :",devices)

    devices = await BleakScanner.discover(timeout=20)
    return devices

async def handle():
    devices = await scan_devices()
    for device in devices:
        address = device.address
        try:
            print(f"Connecting to {address}")
            bc = BleakClient(address,timeout=10)
            print(bc.is_connected)
            # print(bc.get_services())
            res = await bc.pair()
            print(f"Pair results {res}")
            await bc.connect()
            services = await bc.get_services()
            print("Services : ",services)

            while not bc.is_connected:
                asyncio.sleep(1)
            # bc.start_notify
        except BleakDeviceNotFoundError:
            print(f"Device {address} not found!")

# asyncio.run(scan_devices())
asyncio.run(handle())