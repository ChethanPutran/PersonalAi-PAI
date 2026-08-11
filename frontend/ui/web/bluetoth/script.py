import hid

def find_earbuds():
    # List all HID devices
    for device in hid.enumerate():
        print(device)
        # Look for your earbuds - you may need to check vendor/product IDs
        if "Noise" in device.get('product_string', '') or "N1 Pro" in device.get('product_string', ''):
            print(f"Found device: {device}")
            return device
    return None

def monitor_earbuds():
    earbuds = find_earbuds()
    if not earbuds:
        print("Noise N1 Pro not found as HID device")
        return

    try:
        # Open the HID device
        device = hid.device()
        device.open_path(earbuds['path'])
        
        print("Listening for touch events... Press Ctrl+C to stop")
        
        while True:
            # Read data from the device
            data = device.read(64)
            if data:
                print(f"Received data: {data}")
                # Process the data to interpret touch events
                process_touch_event(data)
                
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        device.close()

def process_touch_event(data):
    # You'll need to reverse engineer the data format
    # Typically, media controls send specific byte patterns
    if len(data) >= 3:
        # Example interpretation (adjust based on actual data)
        if data[0] == 0x01 and data[1] == 0xCD:
            print("Play/Pause pressed")
        elif data[0] == 0x01 and data[1] == 0xB3:
            print("Next track pressed")
        elif data[0] == 0x01 and data[1] == 0xB4:
            print("Previous track pressed")

def scan():
    import scapy.all as scapy
    import argparse

        class NetScan:
            
            def __init__(self):
                self.ip
                self.options
                
            def getArgs(self):
                parser = argparse.ArgumentParser()
                parser.add_argument("-t","--target",dest="target",help="Target IP/ IP range.")    
                (self.options,arguments)=parser.parse_args()
                return self.options.target

            def scan(self,ip):
                self.ip = ip
                arpRequest = scapy.ARP(pdst=self.ip)
                broadcast = scapy.Ether(dst="ff:ff:ff:ff:ff:ff")
                packet = broadcast/arpRequest
                answeredList = scapy.srp(packet,timeout=1)[0]
                clientList = []
                
                
                for client in answeredList:
                    clientList.append({"ip":client[1].psrc,"mac":client[1].hwsrc})
                
                return clientList   

            def printClients(self,clientList):
                print("IP\t\t\tMAC Address\n")
                print("-"*50)
                for client in clientList:
                    print(client["ip"]+"\t\t"+client["mac"])     
                        
        scanner = NetScan()
        target = scanner.getArgs()
        clientList = scanner.scan(target)
        scanner.printClients(clientList)

if __name__ == "__main__":
    # monitor_earbuds()
    find_earbuds()