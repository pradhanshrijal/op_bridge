#!/bin/bash

OP_ENVS=$1
CWR_ENVS=$2

OP_ENVS="${OP_ENVS:=${OP_BRIDGE_ROOT}/pha_scripts/export_exploration_envs.sh}"
CWR_ENVS="${CWR_ENVS:=${OP_BRIDGE_ROOT}/pha_scripts/export_carlaware_envs.sh}"

#############################
# Map exploration Mode
# Load the map specified with "FREE_MAP_NAME" and attach the ego vehicle agent to the only vehile in that map 
# No scenario required, the ego vehicle should explore the scene 
#############################

source ${OP_ENVS}
source ${CWR_ENVS}

source /opt/ros/humble/setup.bash 
source ${AUTOWARE_ROOT}/install/setup.bash
python3 ${OP_BRIDGE_ROOT}/op_bridge/op_bridge_ros2.py