#!/usr/bin/python3

import RPi.GPIO as GPIO
import time
GPIO.setmode(GPIO.BCM)

if __name__ == "__main__":
    # Turn GPIO HIGH to keep RUN 
    GPIO.setup(21, GPIO.OUT)
    GPIO.output(21, GPIO.HIGH)

    while True:
        time.sleep(999999)
