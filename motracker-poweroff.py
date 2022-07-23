#!/usr/bin/python3

from luma.oled.device import ssd1306
from luma.core.interface.serial import i2c
serial = i2c(port=1, address=0x3c)
device = ssd1306(serial)

if __name__ == "__main__":
    # Turn OLED off (low power sleep)
    # https://luma-oled.readthedocs.io/en/latest/api-documentation.html
    device.hide()
