idf_component_register(SRCS "radio.c" INCLUDE_DIRS "." REQUIRES
    esp_wifi esp_event esp_netif esp_timer esp_driver_usb_serial_jtag esp_hw_support esp_ringbuf)
