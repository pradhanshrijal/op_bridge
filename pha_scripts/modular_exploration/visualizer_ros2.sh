#!/bin/bash

SOURCE_FILE=$1
SOURCE_FILE="${SOURCE_FILE:=${OP_BRIDGE_ROOT}/pha_scripts/modular_exploration/export_exploration_vars.sh}"

source ${SOURCE_FILE}

source /opt/ros/humble/setup.bash 
source ${AUTOWARE_ROOT}/install/setup.bash
ros2 run rviz2 rviz2 -d ${OP_AGENT_ROOT}/rviz/carla_autoware.rviz -s ${OP_AGENT_ROOT}/rviz/image/autoware.png