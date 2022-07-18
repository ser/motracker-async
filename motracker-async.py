#!/usr/bin/python3

import asyncio
import configparser
import gps.aiogps
import logging
import os
import pendulum
import shortuuid
import smbus
import struct
from datetime import datetime
from influxdb_client import Point as Ipoint
from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync
from luma.oled.device import ssd1306
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from s2sphere import CellId, LatLng
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# configuring logging
logging.basicConfig()
logging.root.setLevel(logging.DEBUG)
logging.getLogger('gps.aiogps').setLevel(logging.ERROR)
logging.getLogger('PIL.PngImagePlugin').setLevel(logging.ERROR)
logging.getLogger('aiosqlite').setLevel(logging.ERROR)

# settings
config = configparser.ConfigParser()
config.read('motracker.ini')

# cell precision level: https://s2geometry.io/resources/s2cell_statistics.html
# our GNSS sensor usually does not provide better estimation 
cell_level = 24  # 0.30 m2

# global values
UPS = 0
LAT = 0
LON = 0
FIX = 0
TIM = ""

# sql database model

Base = declarative_base()

class Track(Base):
    __tablename__ = 'track'
    id = Column(String, primary_key=True)
    points = relationship("Point")

    # required in order to access columns with server defaults
    # or SQL expression defaults, subsequent to a flush, without
    # triggering an expired load
    __mapper_args__ = {"eager_defaults": True}

class Point(Base):
    __tablename__ = 'point'
    id = Column(Integer, primary_key=True)
    trkid = Column(String, ForeignKey("track.id"))
    fix = Column(Integer)
    lat = Column(Float)
    lon = Column(Float)
    speed = Column(Float)
    alt = Column(Float)
    track = Column(Float)
    sep = Column(Float)
    time = Column(DateTime)

# functions

def ll2id(lat, lng):
    l2i = CellId.from_lat_lng(
           LatLng.from_degrees(
            lat, lng)).parent(cell_level).to_token()
    return l2i

# async functions

async def s2sql(async_session, FIX, LAT, LON, SPD, ALT, TIM, TRK, SEP, TID):
    try:
        tim = pendulum.parse(TIM)
        async with async_session() as session:
            async with session.begin():
                session.add(
                    Point(trkid=TID, lat=LAT, lon=LON, speed=SPD, alt=ALT, track=TRK, sep=SEP, time=tim)
                )
            await session.commit()
    except Exception as exc:
        logging.error(f'Error: {exc}')

async def s2inf(FIX, LAT, LON, SPD, ALT, TRK, SEP, TID):
    try:
        async with InfluxDBClientAsync(url=config['influx']['url'],
                                       token=config['influx']['token'],
                                        org=config['influx']['org']) as client:
            write_api = client.write_api()
            _lli = ll2id(LAT, LON)
            _point = Ipoint('moto').tag(
                'id', config['main']['device_name']).tag(
                    's2_cell_id', _lli).field(
                        'fix', FIX).field(
                            'lat', LAT).field(
                                'lon', LON).field(
                                    'speed', SPD).field(
                                        'alt', ALT).field(
                                            'track', TRK).field(
                                                'sep', SEP).field(
                                                    'tid', TID)
            await write_api.write(bucket=config['influx']['bucket'], record=[_point])
    except Exception as exc:
        logging.error(f'Error: {exc}')

async def ledscreen(event):
    serial = i2c(port=1, address=0x3c)
    device = ssd1306(serial)
    while True:
        event.clear()
        load1, load5, load15 = os.getloadavg()
        with canvas(device) as draw:
            draw.rectangle(device.bounding_box, outline="white", fill="black")
            draw.text((5, 4), f"BAT: {UPS:.2f}    FIX: {FIX}", fill="white")
            draw.text((5, 14), f"LAT: {LAT:.9f}", fill="white")
            draw.text((5, 24), f"LON: {LON:.9f}", fill="white")
            draw.text((5, 34), f"{TIM[2:]}", fill="white")
            draw.text((5, 44), f"{load1} {load5} {load15}", fill="white")
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
    global LAT, LON, FIX, TIM

    # DB
    #engine = create_async_engine(config['sql']['db'], echo=True)
    engine = create_async_engine(config['sql']['db'], poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # expire_on_commit=False will prevent attributes from being expired after commit
    async_session = sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    # Track ID
    TID = shortuuid.uuid()

    # DB track init
    async with async_session() as session:
        async with session.begin():
            session.add(
                Track(id=TID)
            )
        await session.commit()
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
                # https://gpsd.io/gpsd_json.html
                #logging.debug(msg)
                try:
                    # TPV mode: 0=unknown, 1=no fix, 2=2D, 3=3D
                    if msg["class"] == "TPV":
                        FIX = msg["mode"]  # fix is always available in TPV
                        LAT = msg.get("lat", 0) 
                        LON = msg.get("lon", 0)
                        SPD = msg.get("speed", 0)
                        ALT = msg.get("altMSL", 0)
                        TIM = msg.get("time", "")
                        TRK = msg.get("track", "0")
                        SEP = msg.get("sep", 0)  # Estimated Spherical (3D) Position Error in meters

                        # send to influx
                        s2inf_task = asyncio.create_task(s2inf(
                            FIX, LAT, LON, SPD, ALT, TRK, SEP, TID))

                        # send to sql
                        s2sql_task = asyncio.create_task(s2sql(
                            async_session, FIX, LAT, LON, SPD, ALT, TIM, TRK, SEP, TID))

                        # display on LED screen
                        event.set()
                except:
                    pass
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
if __name__ == "__main__":
    asyncio.run(main())
