# Cytek Aurora 5L detector channel table: (channel, laser_key, center_nm, width_nm)
# Source: Cytek Aurora Spectral Analyzer config sheet
# https://med.nyu.edu/research/scientific-cores-shared-resources/sites/default/files/cytek-aurora-configuration-file.pdf

CYTEK_5L_CHANNELS = [
    # Ultraviolet 355nm - 16 channels
    ("UV1", "UV355", 373, 15), ("UV2", "UV355", 388, 15), ("UV3", "UV355", 428, 15),
    ("UV4", "UV355", 443, 15), ("UV5", "UV355", 458, 15), ("UV6", "UV355", 473, 15),
    ("UV7", "UV355", 514, 28), ("UV8", "UV355", 542, 28), ("UV9", "UV355", 582, 31),
    ("UV10", "UV355", 613, 31), ("UV11", "UV355", 664, 27), ("UV12", "UV355", 692, 28),
    ("UV13", "UV355", 720, 29), ("UV14", "UV355", 750, 30), ("UV15", "UV355", 780, 30),
    ("UV16", "UV355", 812, 34),
    # Violet 405nm - 16 channels
    ("V1", "Violet405", 428, 15), ("V2", "Violet405", 443, 15), ("V3", "Violet405", 458, 15),
    ("V4", "Violet405", 473, 15), ("V5", "Violet405", 508, 20), ("V6", "Violet405", 525, 17),
    ("V7", "Violet405", 542, 17), ("V8", "Violet405", 581, 19), ("V9", "Violet405", 598, 20),
    ("V10", "Violet405", 615, 20), ("V11", "Violet405", 664, 27), ("V12", "Violet405", 692, 28),
    ("V13", "Violet405", 720, 29), ("V14", "Violet405", 750, 30), ("V15", "Violet405", 780, 30),
    ("V16", "Violet405", 812, 34),
    # Blue 488nm - 14 channels
    ("B1", "Blue488", 508, 20), ("B2", "Blue488", 525, 17), ("B3", "Blue488", 542, 17),
    ("B4", "Blue488", 581, 19), ("B5", "Blue488", 598, 20), ("B6", "Blue488", 615, 20),
    ("B7", "Blue488", 661, 17), ("B8", "Blue488", 679, 18), ("B9", "Blue488", 697, 19),
    ("B10", "Blue488", 717, 20), ("B11", "Blue488", 738, 21), ("B12", "Blue488", 760, 23),
    ("B13", "Blue488", 783, 23), ("B14", "Blue488", 812, 34),
    # Yellow-Green 561nm - 10 channels
    ("YG1", "YG561", 577, 20), ("YG2", "YG561", 598, 20), ("YG3", "YG561", 615, 20),
    ("YG4", "YG561", 661, 17), ("YG5", "YG561", 679, 18), ("YG6", "YG561", 697, 19),
    ("YG7", "YG561", 720, 29), ("YG8", "YG561", 750, 30), ("YG9", "YG561", 780, 30),
    ("YG10", "YG561", 812, 34),
    # Red 640nm - 8 channels
    ("R1", "Red640", 661, 17), ("R2", "Red640", 679, 18), ("R3", "Red640", 697, 19),
    ("R4", "Red640", 717, 20), ("R5", "Red640", 738, 21), ("R6", "Red640", 760, 23),
    ("R7", "Red640", 783, 23), ("R8", "Red640", 812, 34),
]

CYTEK_5L_LASERS = {"UV355": 355, "Violet405": 405, "Blue488": 488, "YG561": 561, "Red640": 640}

assert len(CYTEK_5L_CHANNELS) == 64

LASER_PRESETS = {
    "Emission spectrum only (no laser weighting)": None,
    "Cytek Aurora / Northern Lights 5L (355/405/488/561/640)": CYTEK_5L_LASERS,
    "Generic 3L (405/488/640)": {"Violet405": 405, "Blue488": 488, "Red640": 640},
    "Generic 4L (405/488/561/640)": {"Violet405": 405, "Blue488": 488, "YG561": 561, "Red640": 640},
    "Custom...": "custom",
}
