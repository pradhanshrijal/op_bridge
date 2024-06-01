#!/bin/bash

SOURCE_FILE=$1
SOURCE_FILE="${SOURCE_FILE:=${OP_BRIDGE_ROOT}/pha_scripts/modular_exploration/export_exploration_vars.sh}"

#############################
# Map exploration Mode
# Load the map specified with "FREE_MAP_NAME" and attach the ego vehicle agent to the only vehile in that map 
# No scenario required, the ego vehicle should explore the scene 
#############################

source ${SOURCE_FILE}

source /opt/ros/humble/setup.bash 
source ${AUTOWARE_ROOT}/install/setup.bash
python3 ${OP_BRIDGE_ROOT}/op_bridge/op_bridge_ros2.py