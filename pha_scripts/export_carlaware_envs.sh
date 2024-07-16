#!/bin/bash

# General
export CWR_MAP_PATH="${OP_AGENT_ROOT}/autoware-contents/maps/${FREE_MAP_NAME}"
export CWR_VEHICLE_MODEL="sample_vehicle"
export CWR_SENSOR_MODEL="carla_sensor_kit"
export CWR_DATA_PATH="${DOCKER_SSI}/files/datasets/autoware_data"

# Perception
export CWR_PERCEPTION_MODE="lidar"
# camera_lidar_radar_fusion, camera_lidar_fusion, lidar_radar_fusion, lidar, radar
export CWR_CENTERPOINT_MODEL="centerpoint_tiny"
# `centerpoint`, `centerpoint_tiny` or `centerpoint_sigma`
export CWR_CENTERPOINT_PATH="${CWR_DATA_PATH}/lidar_centerpoint"

# Planning
export CWR_ENABLE_AUTO_MODE="false"
export CWR_SIMULATION="true"