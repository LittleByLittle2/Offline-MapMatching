from ..observation.network import *
from ..observation.trajectory import *
from ..observation.observation import *
from .candidate import *
from .transition import *
from qgis.core import *
import psycopg2
from PyQt5.QtWidgets import QProgressBar, QApplication
from PyQt5.QtCore import QVariant

class HiddenModel:
    
    def __init__(self, trajectory, network):
        self.trajectory = trajectory
        self.network = network
    
    def findViterbiPath(self, maximum_distance, sigma, my, pb):
        #init progressbar
        pb.setValue(0)
        pb.setMaximum(len(self.trajectory.observations))
        
        #init an empty viterbi path to store candidates
        viterbi_path = []
        
        #init the previous observation
        previous_observation = None
        
        #iterate over all observations from our trajectory
        for observation in self.trajectory.observations:
            
            #extract all candidates for the current observation
            candidates = observation.getCandidates(self.network.vector_layer, maximum_distance)
            if len(candidates) == 0:
                return -5
            
            #calculate the probabilities for all candidates to be emitted by the current observation (and vice versa)
            for candidate in candidates:
                candidate.calculateEmittedProbability(observation, sigma, my)
            
            #check whether we have the starting observation or not
            if previous_observation is not None and len(viterbi_path) > 0:
                #get the last entry of the viterbi path
                last_viterbi_entry = viterbi_path[len(viterbi_path) - 1]
                
                #create transitions between candidates and last viterbi vertex
                transitions = []
                for candidate in candidates:
                    transitions.append(Transition(last_viterbi_entry["vertex"], candidate))
                
                #calculate probabilities of the transitions (direction and length) and totalise them
                sum_routing_probability = 0.0
                sum_direction_probability = 0.0
                for transition in transitions:
                    transition.setRoutingProbability(self.network, observation.point.distance(previous_observation.point))
                    transition.setDirectionProbability(previous_observation, observation)
                    
                    #totalise
                    sum_routing_probability += transition.routing_probability
                    sum_direction_probability += transition.direction_probability
                
                #normalize the probabilities of the transitions, i.e. sum of probabilities over all transitions is equal 1
                for transition in transitions:
                    if sum_routing_probability != 0.0:
                        transition.routing_probability = transition.routing_probability / sum_routing_probability
                    if sum_direction_probability != 0.0:
                        transition.direction_probability = transition.direction_probability / sum_direction_probability
                    transition.setTransitionProbability()
                
                #find the highest probability (product of previous prob., trans. prob. and em. prob.)
                max_prob = 0.0
                for transition in transitions:
                    max_prob = (last_viterbi_entry["probability"] * transition.transition_probability * transition.end_candidate.emitted_probability)

                for transition in transitions:
                    if (last_viterbi_entry["probability"] * transition.transition_probability * transition.end_candidate.emitted_probability) == max_prob:
                        
                        #add the candidate with the highest prob. product to the viterbi path
                        viterbi_path.append({"vertex": transition.end_candidate,
                                             "probability": max_prob})
                        break
            else:
                #find the candidate of the start observer with the highest probability
                candidate_with_max_prob = None
                max_prob = 0.0
                for candidate in candidates:
                    if candidate.emitted_probability > max_prob:
                        candidate_with_max_prob = candidate
                        max_prob = candidate.emitted_probability
            
                #add the start vertice to the viterbi path, if we are at the first observation of our trajectory
                viterbi_path.append({"vertex": candidate_with_max_prob,
                                     "probability": max_prob})
            
            #edit the previous observation
            previous_observation = observation
            pb.setValue(pb.value() + 1)
            QApplication.processEvents()
            
        return viterbi_path
    
    def findDijkstraPath(self, host, port, database, user, password, crs, pb):
        #create database connection
        cur = None
        conn = None
        try:
            conn = psycopg2.connect("host=" + host + " port=" + port + " dbname=" + database + " user=" + user + " password=" + password)
            conn.autocommit = True
            cur = conn.cursor()
        except:
            return -99
        
        #create a schema and a table for storing the network
        sql_create_schema = "CREATE SCHEMA omm;"
        sql_create_table = "CREATE TABLE omm.network(id SERIAL PRIMARY KEY, observation1 INTEGER, observation2 INTEGER, cost DOUBLE PRECISION);"
        sql_geom_column = "SELECT addGeometryColumn('omm', 'network', 'geom', " + str(crs) + ", 'POINT', 2);"
        cur.execute(sql_create_schema)
        cur.execute(sql_create_table)
        cur.execute(sql_geom_column)
        
        #enable postgresql extensions
        sql_enable_postgis = "CREATE EXTENSION postgis;"
        sql_enable_pgrouting = "CREATE EXTENSION pgrouting;"
        sq_enable_topology = "CREATE EXTENSION postgis_topology;"
        cur.execute(sql_enable_postgis)
        cur.execute(sql_enable_pgrouting)
        cur.execute(sq_enable_topology)
        
        #init progressbar
        pb.setValue(0)
        pb.maximum(len(self.trajectory.observations) + 5)
        
        #import data to postgis
        previous_observation = None
        for i, observation in enumerate(self.trajectory.observations):
            
            #skip the first observation, because there is no previous observation
            if previous_observation is not None:
                
                #extract all candidates of the current and previous observation
                current_candidates = observation.getCandidates(self.network, maximum_distance)
                previous_candidates = previous_observation.getCandidates(self.network, maximum_distance)
                if len(current_candidates) == 0 or len(previous_candidates) == 0:
                    return -5
                
                #calculate the probabilities for all candidates and previous candidates to be emitted by the current observation (and vice versa)
                for candidate in current_candidates:
                    candidate.calculateEmittedProbability(observation, sigma, my)
                for candidate in previous_candidates:
                    candidate.calculateEmittedProbability(observation, sigma, my)
                
                #create transitions between candidates and previous candidates
                for current_candidate in current_candidates:
                    for previous_candidate in previous_candidates:
                        
                        #create a new transition and calculate probabilites
                        transition = Transition(previous_candidate, current_candidate)
                        transition.setRoutingProbability(self.network, observation.distance(previous_observation.point.x(), previous_observation.point.y()))
                        transition.setDirectionProbability(previous_observation, observation)
                        
                        #insert the new transition as a new linestring into our database table
                        sql_insert_transition = ("INSERT INTO omm.network(observation1, observation2, cost, geom) VALUES(" +
                                                str(i - 1) + ", " + str(i) + ", " + str(transition.transition_probability * 
                                                transition.start_candidate.emitted_probability * transition.end_candidate.emitted_probability) +
                                                ", ST_MakeLine(ST_SetSRID(ST_MakePoint(" + str(transition.start_candidate.point.x) + ", " +
                                                str(transition.start_candidate.point.y) + "), " + str(crs) + "), ST_SetSRID(ST_MakePoint(" +
                                                str(transition.end_candidate.point.x) + ", " + str(transition.end_candidate.point.y) + "), " + str(crs) + ")));")
                        cur.execute(sql_insert_transition)
            pb.setValue(pb.value() + 1)
        
        #create topology for our network
        sql_create_topology = "SELECT topology.CreateTopology('omm_network_topo', " + str(crs) + ", 0.1);"
        sql_add_topology = "SELECT topology.AddTopoGeometryColumn('omm_network_topo', 'omm', 'network', 'topo_geom', 'LINESTRING');"
        cur.execute(sql_create_topology)
        cur.execute(sql_add_topology)
        pb.setValue(pb.value() + 1)
        
        #get all start IDs
        start_ids = []
        sql_select_start_ids = "SELECT id FROM omm.network WHERE observation1 = 0;"
        cur.execute(sql_select_start_ids)
        rows = cur.fetchall()
        for row in rows:
            start_ids.append(row[0])
        pb.setValue(pb.value() + 1)
        
        #get all end IDs
        end_ids = []
        sql_select_end_ids = "SELECT id FROM omm.network WHERE observation1 = " + str(len(self.trajectory.observations)) + ";"
        cur.execute(sql_select_end_ids)
        rows = cur.fetchall()
        for row in rows:
            end_ids.append(row[0])
        pb.setValue(pb.value() + 1)
        
        #routing from each start to each end
        min_cost = 0.0
        start_end = []
        for start_id in start_ids:
            for end_id in end_ids:
                sql_routing = ("SELECT * FROM pgr_dijkstra(" +
                               "'SELECT a.id, a.source, a.target, b.cost FROM edge_table a, omm.network b WHERE a.id = b.id', " +
                               str(start_id) + ", " + str(end_id) + ");")
                cur.execute(sql_routing)
                rows = cur.fetchall()
                for row in rows:
                    if float(row[0]) < min_cost:
                        min_cost = float(row[0])
                        start_end = [start_id, end_id]
        pb.setValue(pb.value() + 1)
        
        #get all points of the shortest track
        sql_final_routing = ("SELECT a.id, a.observation1, b.observation2, a.cost "
                             "FROM omm.network a, " +
                             "(SELECT * FROM pgr_dijkstra('SELECT a.id, a.source, a.target, b.cost FROM edge_table a, omm.network b WHERE a.id = b.id', " +
                             str(start_end[0]) + ", " + str(start_end[1]) + ")) b " +
                             "WHERE a.id = b.id")
        cur.execute(sql_final_routing)
        points = []
        rows = cur.fetchall()
        for row in rows:
            if len(points) == 0:
                points.append({"vertex": self.trajectory.observations[row[1]]},
                              {"probability": row[3]})
            points.append({"vertex": self.trajectory.observations[row[2]]},
                          {"probability": row[3]})
        pb.setValue(pb.value() + 1)
        
        con.close()
        return points
    
    def getPathOnNetwork(self, vertices, pb):
        #create a new layer
        layer = QgsVectorLayer("LineString", "matched trajectory", "memory")
        layer_data = layer.dataProvider()
        layer_data.addAttributes([QgsField("id", QVariant.Int),
                                  QgsField("probability_start_vertex", QVariant.Int),
                                  QgsField("probability_end_vertex", QVariant.Int)])
        layer.updateFields()
        
        #init progressbar
        pb.setValue(0)
        pb.setMaximum(len(vertices))
        
        #iterate over the vertices
        for i, vertex in enumerate(vertices):
            
            #if we are in the first loop, we skip them because we have no previous point to create a routing with start and end
            if i != 0:
                
                #get all points along the shortest way from the previous to the current vertex
                points = self.network.routing(vertices[i - 1]["vertex"], vertex["vertex"])
                if points == -1:
                    return points
                
                #now create a new line feature and add all the points as vertices to this new line
                linestring_vertices = []
                for point in points:
                    linestring_vertices.append(point)
                feature = QgsFeature()
                feature.setGeometry(QgsGeometry.fromPolylineXY(linestring_vertices))
                feature.setAttribute("probability_start_vertex", vertices[i - 1]["probability"])
                feature.setAttribute("probability_end_vertex", vertex["probability"])
                layer.addFeatures([feature])
            
            pb.setValue(pb.value() + 1)
        
        layer.commitChanges()
        return layer
    
