#!/usr/bin/python3

import asyncio
import gps.aiogps
import logging
import pendulum
import smbus
import struct
import sys
from datetime import datetime
from luma.oled.device import ssd1306
from luma.core.interface.serial import i2c
from luma.core.render import canvas

# configuring logging
logging.basicConfig()
logging.root.setLevel(logging.INFO)
# Example of setting up logging level for the aiogps logger
logging.getLogger('gps.aiogps').setLevel(logging.ERROR)

# global values
UPS = 0
LAT = 0
LON = 0

async def ledscreen(event):
    serial = i2c(port=1, address=0x3c)
    device = ssd1306(serial)
    while True:
        event.clear()
        with canvas(device) as draw:
            draw.rectangle(device.bounding_box, outline="white", fill="black")
            draw.text((5, 4), f"B: {UPS:.2f}", fill="white")
            draw.text((5, 14), f"LAT: {LAT:.9f}", fill="white")
            draw.text((5, 24), f"LON: {LON:.9f}", fill="white")
        await asyncio.sleep(0.1)
        await event.wait()

async def piups(event):
    global UPS

    def readCapacity(bus):
        "This function returns as a float the remaining capacity of the battery connected to the Raspi UPS Hat via the provided SMBus object"
        address = 0x32
        read = bus.read_word_data(address, 4)
        swapped = struct.unpack("<H", struct.pack(">H", read))[0]
        capacity = swapped/256-100
        return capacity

    def QuickStart(bus):
        address = 0x32
        bus.write_word_data(address, 0x06,0x4000)

    def PowerOnReset(bus):
        address = 0x32
        bus.write_word_data(address, 0xfe,0x0054)

    bus = smbus.SMBus(1)  # 0 = /dev/i2c-0 (port I2C0), 1 = /dev/i2c-1 (port I2C1)
    QuickStart(bus)

    while True:

        try:
            PowerOnReset(bus)
            await asyncio.sleep(2)
            UPS = readCapacity(bus)
            event.set()
        except:
            await asyncio.sleep(10)
            QuickStart(bus)
            logging.error(f'Error: {exc}')

        await asyncio.sleep(10)

async def main():
    global LAT, LON


    # create event to notify led screen to update
    event = asyncio.Event()

    # OLED SCREEN
    led_task = asyncio.create_task(ledscreen(event))  

    # UPS
    ups_task = asyncio.create_task(piups(event))

    # gpsd main loop
    while True:
        try:
           async with gps.aiogps.aiogps(
                connection_args = {
                    'host': '127.0.0.1',
                    'port': 2947
                },
                connection_timeout = 30,
                reconnect = 1,   # try to reconnect
                alive_opts = {
                    'rx_timeout': 30
                }
            ) as gpsd:
            async for msg in gpsd:
                try:
                    if msg["class"] == "TPV":
                        logging.info(msg)
                        LAT = msg["lat"]
                        LON = msg["lon"]
                        event.set()
                except:
                    pass
                #a = 1
        except asyncio.CancelledError:
           return
        except asyncio.IncompleteReadError:
           logging.info('Connection closed by server')
        except asyncio.TimeoutError:
           logging.error('Timeout waiting for gpsd to respond')
        except Exception as exc:
           logging.error(f'Error: {exc}')

        await asyncio.sleep(0)

#
asyncio.run(main())
