#!/usr/bin/env python

from collections import deque
import math
from Queue import PriorityQueue

import roslib; roslib.load_manifest('corobot_navigation')
import rospy
from geometry_msgs.msg import Point

from corobot_common.srv import GetPixelOccupancy, GetNeighbors, GetLandmark, GetLandmarks, GetCoMap
from corobot_common.msg import Pose, Landmark

#Robot's current position.  Defaults to a test position.
#my_pose = Pose(x=26.896, y=-9.7088, theta=0) # Class3435N
my_pose = Pose(x=27.0, y=-7.0, theta=0) # Close to ATRIUMS4

occ_map = None

#Used to track set goals from a user.
# Queue of (Point, isGoal?) pairs
wp_queue = deque()

def pose_callback(pose):
    """Pose subscription callback"""
    global my_pose
    my_pose = pose

def waypoints_reached_callback(wp):
    """Wayoints Reached subscription callback"""
    top = wp_queue[0]
    if (top[0].x == wp.x) and (top[0].y == wp.y):
        wp_queue.popleft()
        if top[1] == True:
            goal_reached_pub = rospy.Publisher('goals_reached', Point)
            goal_reached_pub.publish(top[0])

def goals_callback(new_goal):
    """No navigation goal queuing"""
    global wp_queue
    wp_queue.append((new_goal, True))
    point_pub = rospy.Publisher('waypoints', Point)
    point_pub.publish(new_goal)

def goals_nav_callback(new_goal):
    """Goals subscription callback."""
    rospy.wait_for_service('get_landmarks')

    try:
        #Publisher to obstacle_avoidance
        point_pub = rospy.Publisher('waypoints', Point)
        get_wps_srv = rospy.ServiceProxy('get_landmarks', GetLandmarks)
        #Gets waypoints, no neighbor data...maybe I should change that ~Karl
        # wps is a Landmark[]
        wps = get_wps_srv().all_wps
        end = new_goal
        #Will return a path of Landmarks from the 
        #   Landmark closest to the robot to the Landmark closest to the goal.
        path = a_star(end, wps)
        for node in path:
            point_pub.publish(x=node.x, y=node.y)
            wp_queue.append((Point(x=node.x, y=node.y), False))
        #And then finally publish the final waypoint
        if len(path) > 0:
            wp_queue.append((new_goal, True))
            point_pub.publish(new_goal)
    except rospy.ServiceException as e:
        rospy.logerr("Service call failed: {}".format(e))

def navigable_to(point, wp):
    """Can I straight line nav to this wp from given point?"""
    cx = point.x
    cy = point.y
    dx = wp.x - cx
    dy = wp.y - cy
    sdx = math.copysign(1, dx)
    sdy = math.copysign(1, dy)
    if dx == 0.0 and dy == 0.0:
        return True
    if math.fabs(dx) > math.fabs(dy):
        incx = sdx / 2.0
        incy = incx * dy / dx
    else:
        incy = sdy / 2.0
        incx = incy * dx / dy
    while sdx * dx > 0 or sdy * dy > 0:
        #If you get an obstacle (if your occupancy prob is greater than 50%) then no path.
        p_x = int(math.floor((cx + dx)/occ_map.info.resolution))
        p_y = int(math.floor((cx + dy)/occ_map.info.resolution))
        #Data is in row-major order
        off = p_y*occ_map.info.width + p_x
        occ = occ_map.data[off]
    
        if occ > 50:
            return False
        dx = dx - incx
        dy = dy - incy
    return True

def distance(x, y):
    """The distance from the origin to (x, y)."""
    return math.sqrt(x * x + y * y)

def point_distance(p1, p2):
    """Distance between two point-like objects in the Euclidean plane."""
    return distance(p2.x - p1.x, p2.y - p1.y)

def find_nearest_navigable(point, wps):
    """Find nearest visible Landmark to the given point-like object.

    Arguments:
    point -- The starting point
    wps -- Landmark[] with all waypoints in the graph/map

    Returns a Landmark:
        Nearest navigable Landmark
        None if no nearby waypoint can be found

    """
    closest = None
    for wp in wps:
        if closest is None and navigable_to(point, wp):
            closest = (point_distance(point, wp), wp)
            continue
        dist = point_distance(point, wp)
        if closest is not None and dist < closest[0] and navigable_to(point, wp):
            closest = (dist, wp)
    if closest == None:
        rospy.logerr("Cannot find a nearby waypoint to begin navigation!")
        return None
    return closest[1]

def a_star(dest, wps):
    """Perform A* to produce path of waypoints to given dest from nearest map waypoint.

    Arguments:
    dest -- Destination Point
    wps  -- List of Landmarks (Landmark[]) representing full list of map waypoints.

    Returns:
        Landmark[] representing path to follow to the destination.
        Empty list if no path can be found.

    """
    near = find_nearest_navigable(my_pose, wps)
    goal = find_nearest_navigable(dest, wps)
    if near is None:
        rospy.logerr("A* navigation failed, couldn't find a starting node.")
        return []
    rospy.logdebug("LandmarkClosestToMe: {}".format(near.name))
    preds = {near.name: None}
    pq = PriorityQueue()
    open_set = [near]
    visited = []
    #dict holding {waypoint name: distance from robot to waypoint} pairs
    g_scores = {near.name: point_distance(my_pose, near)}
    #pq elements are (g+h, node)
    # g=distRobotWp, h=distWpGoal
    pq.put((g_scores[near.name] + point_distance(near, goal),
        near))
    #Set up persistent connection to the GetNeighbors service
    rospy.wait_for_service('get_neighbors')
    get_nbrs_srv = rospy.ServiceProxy('get_neighbors', GetNeighbors, persistent=True)
    try:
        while not pq.empty():
            curr = pq.get()
            cnode = curr[1]
            
            if cnode.name == goal.name:
                #Found the path! Now build it.
                path = []
                pnode = goal
                while pnode is not None:
                    pname = pnode.name
                    path.insert(0, pnode)
                    pnode = preds[pname]
                return path

            open_set.remove(cnode)
            visited.append(cnode)
            for nbr in get_nbrs_srv(cnode).neighbors:
                if nbr in visited:
                    continue
                tentG = g_scores[cnode.name] + point_distance(cnode, nbr)
                if nbr not in open_set or tentG < g_scores[nbr.name]:
                    preds[nbr.name] = cnode
                    g_scores[nbr.name] = tentG
                    pq.put((g_scores[nbr.name] + point_distance(nbr, goal), nbr))
                    if nbr not in open_set:
                        open_set.append(nbr)
    except rospy.ServiceProxy as e:
        rospy.logerr("Service call failed: {}".format(e))
    finally:
        get_nbrs_srv.close()
    return []

def main():
    rospy.init_node('robot_navigator')
    #Publisher to obstacle_avoidance
    point_pub = rospy.Publisher('waypoints', Point)
    rospy.Subscriber('goals_nav', Point, goals_nav_callback)
    rospy.Subscriber('goals', Point, goals_callback)
    rospy.Subscriber('pose', Pose, pose_callback)

    global occ_map
    rospy.wait_for_service('get_map')
    try:
        get_map_srv = rospy.ServiceProxy('get_map',GetCoMap)
        occ_map = get_map_srv().map
    except rospy.ServiceProxy as e:
        rospy.logerr("Service call failed: {}".format(e))

    rospy.spin()

if __name__ == '__main__':
    main()