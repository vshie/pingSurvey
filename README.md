# BlueOS Ping2 Survey Extension

A simple extension for BlueOS that enables time-synchronized collection of sonar data from the Ping2 sonar, alongside GPS data. This extension provides both a full interface and a compact widget view for monitoring and recording survey data.

## Features

- Real-time sonar depth and confidence monitoring
- GPS position tracking
- Vessel heading display
- Data logging at 2Hz
- CSV data export
- Compact widget view for Cockpit integration
- Automatic system ID detection
- Support for multiple autopilot types (ArduPilot, PX4, Generic)

## Installation

1. Install the extension through the BlueOS extension manager
2. Follow the guide here: https://bluerobotics.com/learn/collecting-creating-bathymetry-blueboat-ping2/ 

## Usage

### Full Interface
Access the full interface by navigating to the menu entry in BlueOS for Simple PingSurvey

### Cockpit Widget Integration
To add the compact widget to your Cockpit interface:

1. Open Cockpit's edit interface
2. Add a new iframe widget
3. Set the iframe URL to:
```
http://your-vehicle-ip:5420/widget
```

### Data Collection
1. Click the play button to start data collection
2. The extension will automatically detect the correct system ID and begin logging
3. Data is saved to a CSV file with timestamps
4. Use the download button to retrieve the collected data

## Data Format
The CSV file includes:
- Unix Timestamp
- Date
- Time
- Depth (cm)
- Confidence (%)
- Vessel heading (degrees)
- Latitude
- Longitude

## Future Features
- Fix bar at top that scrolls down to hide
- WP Survey speed parameter control on page
- User controlled confidence filter on live and/or logged data
- Better mobile experience - scaling corner logos

## Discussion
For more information and updates, visit the [Blue Robotics discussion thread](https://discuss.bluerobotics.com/t/alpha-release-simple-ping2-survey-extension/15794)
