#!/usr/bin/env python
#
# Copyright (c) 2019 Intel Corporation
#
# This work is licensed under the terms of the MIT license.
# For a copy, see <https://opensource.org/licenses/MIT>.
#

"""
This module provides a ROS autonomous agent interface to control the ego vehicle via a ROS stack
"""
import math
import os
import subprocess
import signal
import threading
import datetime
import time
import numpy
import numpy as np
import carla
from transforms3d.euler import euler2mat, quat2euler, euler2quat
import rclpy
from rclpy.clock import ClockType
# from rclpy.time import Time
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from rclpy.task import Future

from cv_bridge import CvBridge
from geometry_msgs.msg import PoseStamped, TwistWithCovariance, TwistStamped, TwistWithCovarianceStamped
from nav_msgs.msg import Odometry, Path
from rosgraph_msgs.msg import Clock
from builtin_interfaces.msg import Time
from sensor_msgs.msg import Image, PointCloud2, NavSatFix, NavSatStatus, CameraInfo, Range, PointField, Imu
from sensor_msgs_py.point_cloud2 import create_cloud
from std_msgs.msg import Header, String
from srunner.scenariomanager.carla_data_provider import *
from leaderboard.autoagents.autonomous_agent import AutonomousAgent, Track
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy
from autoware_auto_vehicle_msgs.msg import ControlModeReport, GearReport, SteeringReport, TurnIndicatorsReport, HazardLightsReport, VelocityReport
from autoware_auto_control_msgs.msg import AckermannControlCommand


def get_entry_point():
    return 'Ros2Agent'

class Ros2Agent(AutonomousAgent):

    """
    Base class for ROS-based stacks.

    Derive from it and implement the sensors() method.

    Please define OP_AGENT_ROOT in your environment.
    The stack is started by executing $OP_AGENT_ROOT/start.sh

    The sensor data is published on similar topics as with the carla-ros-bridge. You can find details about
    the utilized datatypes there.

    This agent expects a roscore to be running.
    """

    speed = None
    current_control = None
    stack_process = None    
    current_map_name = None
    step_mode_possible = None
    vehicle_info_publisher = None
    global_plan_published_time = None
    start_script = None
    manual_data_debug = False
    counter = 0
    open_drive_map_data = None
    open_drive_map_name = None
    steering_factor = 0.45
    max_steer_angle = 0.7
    lidar_freq = 11
    camera_freq = 11
    imu_freq = 50
    can_freq = 50
    gnss_freq = 2


    def setup(self, path_to_conf_file):
        """
        setup agent
        """
        self.track = Track.MAP
        self.agent_role_name = os.environ['AGENT_ROLE_NAME']
        self.bridge_mode = os.environ['OP_BRIDGE_MODE']
        self.topic_base = "/carla/{}".format(self.agent_role_name)
        self.topic_waypoints = self.topic_base + "/waypoints"
        self.stack_thread = None
        self.counter = 0
        self.open_drive_map_name = None
        self.open_drive_map_data = None      
        self.lidar_publish_prev_time = datetime.datetime.now()
        self.camera_publish_prev_time = datetime.datetime.now()
        self.imu_publish_prev_time = datetime.datetime.now()
        self.can_publish_prev_time = datetime.datetime.now()
        self.gnss_publish_prev_time = datetime.datetime.now()
                
        # get start_script from environment
        team_code_path = os.environ['OP_AGENT_ROOT']
        if not team_code_path or not os.path.exists(team_code_path):
            raise IOError("Path '{}' defined by OP_AGENT_ROOT invalid".format(team_code_path))
        self.start_script = "{}/pha_scripts/cwr_chassis_ros2.sh".format(team_code_path)
        if not os.path.exists(self.start_script):
            raise IOError("File '{}' defined by OP_AGENT_ROOT invalid".format(self.start_script))

        # initialize ros2 node
        rclpy.init(args=None)
        self.ros2_node = rclpy.create_node("op_ros2_agent")
        

        self.clock_publisher = self.ros2_node.create_publisher(Clock, "/clock", 10)
        obj_clock = Clock()
        obj_clock.clock = Time(sec=0)
        self.clock_publisher.publish(obj_clock)
        
        self.timestamp = None
        self.speed = 0
        #publish global path every 2 seconds
        self.global_plan_published_time = 0 
        
        self.vehicle_status_publisher = None     
        self.auto_velocity_status_publisher = None 
        self.auto_steering_status_publisher = None  
        self.auto_gear_status_publisher = None
        self.auto_control_mode_publisher = None

        self.vehicle_twist_publisher = None        
        self.vehicle_imu_publisher = None              
        self.map_file_publisher = None
        self.current_map_name = None        
        self.step_mode_possible = False
        self.perception_cloud_publisher = None
        self.localization_cloud_publisher = None
        self.sensing_cloud_publisher = None

        self.publisher_map = {}
        self.id_to_sensor_type_map = {}
        self.id_to_camera_info_map = {}
        self.cv_bridge = CvBridge()        

        # self.qos_profile = QoSProfile(depth=1)
        # self.qos_profile.durability = DurabilityPolicy.TRANSIENT_LOCAL
        
        self.vehicle_control_subscriber = self.ros2_node.create_subscription(
            TwistStamped, '/carla_op_controller_cmd', self.on_vehicle_control, 1)
        
        self.autoware_universe_vehicle_control_subscriber = self.ros2_node.create_subscription(
            AckermannControlCommand, 'control/command/control_cmd', self.on_autoware_universe_vehicle_control,
            qos_profile=QoSProfile(depth=1))

        self.current_control = carla.VehicleControl()

        self.waypoint_publisher = self.ros2_node.create_publisher(Path, self.topic_waypoints, 1)

        for sensor in self.sensors():
            self.id_to_sensor_type_map[sensor['id']] = sensor['type']
            if sensor['type'] == 'sensor.camera.rgb':
                self.publisher_map[sensor['id']] = self.ros2_node.create_publisher(
                    Image, "/sensing/camera/traffic_light/image_raw", 1)
                self.id_to_camera_info_map[sensor['id']] = self.build_camera_info(sensor)
                self.publisher_map[sensor['id'] + '_info'] = self.ros2_node.create_publisher(
                    CameraInfo, "/sensing/camera/traffic_light/camera_info", 1)
            elif sensor['type'] == 'sensor.lidar.ray_cast':
                self.sensing_cloud_publisher = self.ros2_node.create_publisher(
                    PointCloud2, '/carla_pointcloud', 10)
            elif sensor['type'] == 'sensor.other.gnss':
                self.publisher_map[sensor['id']] = self.ros2_node.create_publisher(
                    NavSatFix,  "/carla_nav_sat_fix", 1)
            elif sensor['type'] == 'sensor.speedometer':                
                if not self.vehicle_status_publisher:
                    self.vehicle_status_publisher = self.ros2_node.create_publisher(
                        Odometry, '/odo', 1)
                if not self.auto_velocity_status_publisher:
                    self.auto_velocity_status_publisher = self.ros2_node.create_publisher(
                        VelocityReport, '/vehicle/status/velocity_status', 1)
                if not self.auto_steering_status_publisher:
                    self.auto_steering_status_publisher = self.ros2_node.create_publisher(
                        SteeringReport, '/vehicle/status/steering_status', 1)
                if not self.auto_gear_status_publisher:
                    self.auto_gear_status_publisher = self.ros2_node.create_publisher(
                        GearReport, '/vehicle/status/gear_status', 1)
                if not self.auto_control_mode_publisher:
                    self.auto_control_mode_publisher = self.ros2_node.create_publisher(
                        ControlModeReport, '/vehicle/status/control_mode', 1)
            elif sensor['type'] == 'sensor.other.imu':                
                if not self.vehicle_imu_publisher:
                    self.vehicle_imu_publisher = self.ros2_node.create_publisher(
                        Imu, '/sensing/imu/tamagawa/imu_raw', 1)
            elif sensor['type'] == 'sensor.opendrive_map':                                
                if not self.map_file_publisher:
                    self.map_file_publisher = self.ros2_node.create_publisher(String, '/carla/map_file', 1)                
            else:
                raise TypeError("Invalid sensor type: {}".format(sensor['type']))    
        
        self.spin_thread = threading.Thread(target=rclpy.spin, args=(self.ros2_node,))
        self.spin_thread.start()

    def init_local_agent(self, role_name, map_name, waypoints_topic_name, enable_explore):
        # rospy.loginfo("Executing stack...")
        print("Executing stack...", role_name, map_name)
        local_start_script = self.start_script + ' ' + role_name + ' ' + map_name + ' ' + enable_explore + ' ' + waypoints_topic_name
        self.stack_process = subprocess.Popen(local_start_script, shell=True, preexec_fn=os.setpgrp)
        # self.vehicle_control_event = threading.Event()

    def write_opendrive_map_file(self, map_name, map_data):

        team_code_path = os.environ['OP_AGENT_ROOT']
        if not team_code_path or not os.path.exists(team_code_path):
            raise IOError("Path '{}' defined by OP_AGENT_ROOT invalid".format(team_code_path))
        opendrive_map_path = "{}/autoware-contents/maps/vector_maps/{}.xodr".format(team_code_path, map_name)        

        f = open(opendrive_map_path, "w")
        f.write(map_data)
        f.close()
    
    def on_autoware_universe_vehicle_control(self, data):
        """
        callback if a new vehicle control command is received
        """        

        cmd = carla.VehicleControl()  
        cmd.steer = (-data.lateral.steering_tire_angle / self.max_steer_angle)*self.steering_factor
        speed_diff = data.longitudinal.speed - self.speed 
        if speed_diff > 0:            
            cmd.throttle = 0.75           
            cmd.brake = 0.0   
        elif speed_diff < 0.0:
            cmd.throttle = 0.0
            if data.longitudinal.speed <= 0.0 :                
                cmd.brake = 0.75  
            elif  speed_diff > -1:
                cmd.brake = 0.0
            else :
                cmd.brake = 0.01
        
        self.current_control = cmd
        self.step_mode_possible = True

    def checkFrequecy(self, prev_time, target_freq):
        time_delta = (datetime.datetime.now() - prev_time).microseconds/1000000.0            
        if 1.0/time_delta >= target_freq:
            return True        
        return False

    def on_vehicle_control(self, data):
        """
        callback if a new vehicle control command is received
        """        
        cmd = carla.VehicleControl()        
        cmd.throttle = data.twist.linear.x/100.0
        cmd.steer = data.twist.angular.z/100.0
        cmd.brake = data.twist.linear.y/100.0        
        self.current_control = cmd
        self.step_mode_possible = True

    def build_camera_info(self, attributes):  
        """
        Private function to compute camera info

        camera info doesn't change over time
        """
        camera_info = CameraInfo()
        # store info without header
        # camera_info.header = None
        camera_info.width = int(attributes['width'])
        camera_info.height = int(attributes['height'])
        camera_info.distortion_model = 'plumb_bob'
        cx = camera_info.width / 2.0
        cy = camera_info.height / 2.0
        fx = camera_info.width / (
            2.0 * math.tan(float(attributes['fov']) * math.pi / 360.0))
        fy = fx
        camera_info.k = [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0]
        camera_info.d = [0.0, 0.0, 0.0, 0.0, 0.0]
        camera_info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        camera_info.p = [fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0]
        return camera_info

    def publish_plan(self):
        """
        publish the global plan
        """
        msg = Path()
        msg.header = self.get_header()
        msg.header.frame_id = "map"
        
        for wp in self._global_plan_world_coord:
            pose = PoseStamped()
            pose.pose.position.x = wp[0].location.x
            pose.pose.position.y = -wp[0].location.y
            pose.pose.position.z = wp[0].location.z
            quaternion = euler2quat(0, 0, -math.radians(wp[0].rotation.yaw))
            pose.pose.orientation.x = quaternion[0]
            pose.pose.orientation.y = quaternion[1]
            pose.pose.orientation.z = quaternion[2]
            pose.pose.orientation.w = quaternion[3]
            msg.poses.append(pose)

        #rospy.loginfo("Publishing Plan...")
        self.waypoint_publisher.publish(msg)

    def sensors(self):
        sensors = [{'type': 'sensor.camera.rgb', 'x': 0.7, 'y': 0.0, 'z': 1.6, 'roll': 0.0, 'pitch': 0.0, 'yaw': 0.0,
            'width': 1280, 'height': 720, 'fov': 100, 'id': 'Center'},
            {'type': 'sensor.lidar.ray_cast', 'x': 0.0, 'y': 0.0, 'z': 2.6, 'roll': 0.0, 'pitch': 0.0,
             'yaw': -90.0, 'id': 'LIDAR'},
            {'type': 'sensor.other.gnss', 'x': 0.0, 'y': 0.0, 'z': 1.6, 'id': 'GPS'},
            {'type': 'sensor.opendrive_map', 'reading_frequency': 1, 'id': 'OpenDRIVE'},
            {'type': 'sensor.speedometer', 'reading_frequency': 10, 'id': 'speed'},
            {'type': 'sensor.other.imu', 'x': 0.0, 'y': 0.0, 'z': 1.6, 'roll': 0.0, 'pitch': 0.0,
             'yaw': 0.0, 'id': 'IMU'},
            ]
        return sensors

    def get_header(self):
        """
        Returns ROS message header
        """
        header = Header()
        seconds = int(self.timestamp)
        nanoseconds = int((self.timestamp - int(self.timestamp)) * 1000000000.0)
        header.stamp = Time(sec=seconds, nanosec=nanoseconds)            
        return header

    def publish_lidar(self, sensor_id, data):
        """
        Function to publish lidar data
        """
        if self.checkFrequecy(self.lidar_publish_prev_time, self.lidar_freq) == True:
            return
        
        self.lidar_publish_prev_time = datetime.datetime.now()

        header = self.get_header()
        lidar_data = numpy.frombuffer(data, dtype=numpy.float32)        

        if lidar_data.shape[0] % 4 == 0:
            lidar_data = numpy.reshape(lidar_data, (int(lidar_data.shape[0] / 4), 4))
            lidar_data = lidar_data[..., [1, 0, 2, 3]]
            fields = [PointField(name='x', offset=0,
                         datatype=PointField.FLOAT32, count=1),
                        PointField(name='y', offset=4,
                         datatype=PointField.FLOAT32, count=1),
                        PointField(name='z', offset=8,
                         datatype=PointField.FLOAT32, count=1),
                        PointField(name='intensity', offset=12,
                         datatype=PointField.FLOAT32, count=1)]
            
            header.frame_id = 'velodyne_top'
            msg = create_cloud(header,fields, lidar_data)
            self.sensing_cloud_publisher.publish(msg)   
        else:
            print('Cannot Reshape LIDAR Data buffer')

    def publish_gnss(self, sensor_id, data):
        """
        Function to publish gnss data
        """

        if self.checkFrequecy(self.gnss_publish_prev_time, self.gnss_freq) == True:
            return
        
        self.gnss_publish_prev_time = datetime.datetime.now()

        msg = NavSatFix()
        msg.header = self.get_header()
        msg.header.frame_id = 'gnss_link'
        msg.latitude = data[0]
        msg.longitude = data[1]
        msg.altitude = data[2] 
        # msg.altitude = data[2] 
        msg.status.status = NavSatStatus.STATUS_SBAS_FIX
        # pylint: disable=line-too-long
        msg.status.service = NavSatStatus.SERVICE_GPS | NavSatStatus.SERVICE_GLONASS | NavSatStatus.SERVICE_COMPASS | NavSatStatus.SERVICE_GALILEO
        # pylint: enable=line-too-long
        self.publisher_map[sensor_id].publish(msg)

    def publish_camera(self, sensor_id, data):
        """
        Function to publish camera data
        """        
    
        if self.checkFrequecy(self.camera_publish_prev_time, self.camera_freq) == True:
            return
        
        self.camera_publish_prev_time = datetime.datetime.now()
        

        msg = self.cv_bridge.cv2_to_imgmsg(data, encoding='bgra8')
        # the camera data is in respect to the camera's own frame
        msg.header = self.get_header()
        msg.header.frame_id = 'traffic_light_left_camera/camera_link'

        cam_info = self.id_to_camera_info_map[sensor_id]
        cam_info.header = msg.header
        self.publisher_map[sensor_id + '_info'].publish(cam_info)
        self.publisher_map[sensor_id].publish(msg)

    def publish_imu(self, sensor_id, data):
        """
        Publish IMU data 
        """        

        if self.checkFrequecy(self.imu_publish_prev_time, self.imu_freq) == True:
            return
        
        self.imu_publish_prev_time = datetime.datetime.now()
        
        imu_msg = Imu()
        imu_msg.header = self.get_header()
        imu_msg.header.frame_id = "tamagawa/imu_link"

        imu_msg.linear_acceleration.x = data[0]
        imu_msg.linear_acceleration.y = -data[1]
        imu_msg.linear_acceleration.z = data[2]
        
        imu_msg.angular_velocity.x = -data[3]
        imu_msg.angular_velocity.y = data[4]
        imu_msg.angular_velocity.z = -data[5]
        
        imu_rotation = data[6]

        quaternion = euler2quat(0, 0, -math.radians(imu_rotation))
        imu_msg.orientation.x = quaternion[0]
        imu_msg.orientation.y = quaternion[1]
        imu_msg.orientation.z = quaternion[2]
        imu_msg.orientation.w = quaternion[3]
        
        self.vehicle_imu_publisher.publish(imu_msg)

    def publish_can(self, sensor_id, data):
        """
        publish can data
        """    

        if self.checkFrequecy(self.can_publish_prev_time, self.can_freq) == True:
            return
        
        self.can_publish_prev_time = datetime.datetime.now()

        self.speed = data['speed']
        twist_msg = TwistWithCovariance()        
        twist_msg.twist.linear.x = data['speed']        
        if twist_msg.twist.linear.x < 0.0:
            twist_msg.twist.linear.x = 0.0
        twist_msg.twist.angular.z = -self.current_control.steer
        twist_msg.twist.linear.z = 1.0 # to tell OpenPlanner to use the steer directly
        twist_msg.twist.angular.x = 1.0 # to tell OpenPlanner to use the steer directly 
        
        odo_msg = Odometry()
        odo_msg.header = self.get_header()
        odo_msg.twist = twist_msg

        vel_rep = VelocityReport()
        vel_rep.header = self.get_header()
        vel_rep.header.frame_id = "base_link"
        vel_rep.longitudinal_velocity = data['speed'];                                 
        vel_rep.heading_rate = 0.0
        self.auto_velocity_status_publisher.publish(vel_rep)

        steer_rep = SteeringReport()
        steer_rep.steering_tire_angle = (-self.current_control.steer * self.max_steer_angle)/self.steering_factor
        self.auto_steering_status_publisher.publish(steer_rep)

        gear_rep = GearReport()
        gear_rep.stamp = self.get_header().stamp
        gear_rep.report = GearReport.DRIVE        
        self.auto_gear_status_publisher.publish(gear_rep)

        control_mode_rep = ControlModeReport()
        control_mode_rep.stamp = self.get_header().stamp
        control_mode_rep.mode = ControlModeReport.AUTONOMOUS        
        self.auto_control_mode_publisher.publish(control_mode_rep)
        

    def publish_hd_map(self, sensor_id, data, map_name):
        """
        publish hd map data
        """                 
        if self.current_map_name != map_name:
            self.current_map_name = map_name        
        if self.map_file_publisher:
            data_msg = String()
            data_msg.data = data['opendrive']
            self.map_file_publisher.publish(data_msg)

    def use_stepping_mode(self):  
        """
        Overload this function to use stepping mode!
        """
        return False

    def run_step(self, input_data, timestamp):
        """
        Execute one step of navigation.
        """        
        town_map_name = self._get_map_name(CarlaDataProvider.get_map().name)
        if self.stack_process is None and town_map_name is not None and self.open_drive_map_data is not None:
            self.write_opendrive_map_file(self.open_drive_map_name, self.open_drive_map_data)
            if self.bridge_mode == 'free' or self.bridge_mode == 'srunner':
                self.init_local_agent(self.agent_role_name, town_map_name, '', 'true')
            elif self.bridge_mode == 'leaderboard':
                self.init_local_agent(self.agent_role_name, town_map_name, self.topic_waypoints, 'false')
            else:
                self.init_local_agent(self.agent_role_name, town_map_name, self.topic_waypoints, 'false')

            if self._global_plan_world_coord:                
                self.publish_plan()
            
        
        # self.vehicle_control_event.clear()
        self.timestamp = timestamp
        seconds = int(self.timestamp)
        nanoseconds = int((self.timestamp - int(self.timestamp)) * 1000000000.0)
        obj_clock = Clock()
        obj_clock.clock = Time(sec=seconds, nanosec=nanoseconds)
       
        self.clock_publisher.publish(obj_clock)

        # check if stack is still running
        if self.stack_process and self.stack_process.poll() is not None:
            raise RuntimeError("Stack exited with: {} {}".format(
                self.stack_process.returncode, self.stack_process.communicate()[0]))
        
        #wait 2 second before publish the global path  
        if self._global_plan_world_coord and (self.timestamp - self.global_plan_published_time) > 2.0:
            self.global_plan_published_time = self.timestamp      
            self.publish_plan()

        # publish data of all sensors
        for key, val in input_data.items():
            sensor_type = self.id_to_sensor_type_map[key]            
            if self.manual_data_debug:
                print(key)

            if sensor_type == 'sensor.camera.rgb':
                self.publish_camera(key, val[1])
            elif sensor_type == 'sensor.opendrive_map':      
                # extract map name                            
                self.open_drive_map_data = val[1]['opendrive']
                self.open_drive_map_name = self._get_map_name(CarlaDataProvider.get_map().name)
                self.publish_hd_map(key, val[1], self.open_drive_map_name) #Extract dictionary with map data and transform and odometry                   
            elif sensor_type == 'sensor.other.gnss':
                self.publish_gnss(key, val[1])
            elif sensor_type == 'sensor.lidar.ray_cast':
                self.publish_lidar(key, val[1])
            elif sensor_type == 'sensor.speedometer':
                self.publish_can(key, val[1])
            elif sensor_type == 'sensor.other.imu':                
                self.publish_imu(key, val[1])
            elif self.manual_data_debug:
                print('Additional Sensor !! ') 
                print(key)

        # for leaderboard debugging 
        # count_out = 500
        # # if self.open_drive_map_name == 'Town01' or self.open_drive_map_name == 'Town03':
        # #     count_out = 200

        # if self.counter > count_out:
        #     raise TypeError("Just Stop ................. Please ")
        # self.counter = self.counter + 1

        # if self.open_drive_map_name == 'Town01' :
        #     raise TypeError("Just Stop ................. Please ")

        return self.current_control

    def destroy(self):
        """
        Cleanup of all ROS publishers
        """        
        if self.stack_process and self.stack_process.poll() is None:            
            print("Sending SIGTERM to stack...")
            os.killpg(os.getpgid(self.stack_process.pid), signal.SIGTERM)
            print("Waiting for termination of stack...")
            self.stack_process.wait()
            print("Terminated stack in 5 .. 4 .. 3 .. 2 .. 1 ")
            time.sleep(2)

    def _get_map_name(self, map_full_name):

        if map_full_name is None:
            return None
        name_start_index = map_full_name.rfind("/")
        if name_start_index == -1:
            name_start_index = 0
        else:
            name_start_index = name_start_index + 1        

        return map_full_name[name_start_index:len(map_full_name)]



  
