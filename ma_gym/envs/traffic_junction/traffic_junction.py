# -*- coding: utf-8 -*-

import copy
import logging
import random

import gym
import numpy as np
from gym import spaces
from gym.utils import seeding

from ..utils.action_space import MultiAgentActionSpace
from ..utils.observation_space import MultiAgentObservationSpace
from ..utils.draw import draw_grid, fill_cell, write_cell_text

logger = logging.getLogger(__name__)

# TODO do i need to set the agents position to (0, 0) after they are removed or can they virtually stay in the same place?
# if they stay there do i need to verify everytime if it is on the road when making moves?
class TrafficJunction(gym.Env):
    """
    This consists of a 4-way junction on a 14 × 14 grid. At each time step, new cars enter the grid with
    probability `p_arrive` from each of the four directions. However, the total number of cars at any given
    time is limited to `Nmax = 10`.

    Each car occupies a single cell at any given time
    and is randomly assigned to one of three possible routes (keeping to the right-hand side of the road).
    At every time step, a car has two possible actions: gas which advances it by one cell on its route or
    brake to stay at its current location. A car will be removed once it reaches its destination at the edge
    of the grid.

    Two cars collide if their locations overlap. A collision incurs a reward `rcoll = −10`, but does not affect
    the simulation in any other way. To discourage a traffic jam, each car gets reward of `τrtime = −0.01τ`
    at every time step, where `τ` is the number time steps passed since the car arrived. Therefore, the total
    reward at time t is

    Each car is represented by one-hot binary vector set {n, l, r}, that encodes its unique ID, current location
    and assigned route number respectively. Each agent controlling a car can only observe other cars in its vision
    range (a surrounding 3 × 3 neighborhood), but it can communicate to all other cars.

    The state vector s_j for each agent is thus a concatenation of all these vectors, having dimension
    32 × |n| × |l| × |r|.

    Reference : Learning Multiagent Communication with Backpropagation
    Url : https://papers.nips.cc/paper/6398-learning-multiagent-communication-with-backpropagation.pdf
    """
    metadata = {'render.modes': ['human', 'rgb_array']}

    def __init__(self, grid_shape=(14, 14), n_agents=4, n_max=4, rcoll=-10, arrive_prob=0.5, full_observable=False):
        self._grid_shape = grid_shape
        self.n_agents = n_max
        self._max_steps = 100
        self._step_count = None
        self._collision_reward = rcoll
        self._total_episode_reward = None
        self._arrive_prob = arrive_prob
        self._n_max = n_max
        self._inital_agents = n_agents

        self._agent_view_mask = (3, 3)
        mask_size = np.prod(self._agent_view_mask)

        self.curr_cars_count = 0

        self._step_cost = 0

        self._n_gates = 4
        self._entry_gates = [(self._grid_shape[0] // 2, 0), (self._grid_shape[0] - 1, self._grid_shape[1] // 2),\
                             (0, self._grid_shape[1] // 2 - 1), (self._grid_shape[0] // 2 - 1, self._grid_shape[1] - 1)]  # [(7, 0), (13, 7), (0, 6), (6, 13)]
        
        self._destination = [(self._grid_shape[0] // 2, self._grid_shape[1] - 1), (0, self._grid_shape[1] // 2),\
                             (self._grid_shape[0] - 1, self._grid_shape[1] // 2 - 1), (self._grid_shape[0] // 2 - 1, 0)]  # [(7, 13), (0, 7), (13, 6), (6, 0)]
        
        self._turning_places = {(0, 1): ((self._grid_shape[0] // 2, self._grid_shape[0] // 2 - 1), (self._grid_shape[0] // 2, self._grid_shape[0] // 2)),\
                                (-1, 0): ((self._grid_shape[0] // 2, self._grid_shape[0] // 2), (self._grid_shape[0] // 2 - 1, self._grid_shape[0] // 2)),\
                                (1, 0): ((self._grid_shape[0] // 2 - 1, self._grid_shape[0] // 2 - 1), (self._grid_shape[0] // 2, self._grid_shape[0] // 2 - 1)),\
                                (0, -1): ((self._grid_shape[0] // 2 - 1, self._grid_shape[0] // 2), (self._grid_shape[0] // 2 - 1, self._grid_shape[0] // 2 - 1))} #[((7, 6), (7,7))), ((7, 7),(6,7)), ((6,6),(7, 6)), ((6, 7),(6,6))]
        
        self._agent_turned = [False for _ in range(self.n_agents)]
        
        self._route_vectors = {(self._grid_shape[0] // 2, 0): (0, 1), (self._grid_shape[0] - 1, self._grid_shape[0] // 2): (-1, 0),\
                                (0, self._grid_shape[0] // 2 - 1): (1, 0), (self._grid_shape[0] // 2 - 1, self._grid_shape[0] - 1): (0, -1)}

        self._agents_routes = [-1 for _ in range(self.n_agents)]
        self._agents_direction = [0 for _ in range(self.n_agents)]

        self.action_space = MultiAgentActionSpace([spaces.Discrete(2) for _ in range(self.n_agents)])
        self.agent_pos = {_: None for _ in range(self.n_agents)}
        self.on_the_road = [False for _ in range(self.n_agents)]

        self._full_obs = self.__create_grid()
        self._base_img = self.__draw_base_img()
        self._agent_dones = [None for _ in range(self.n_agents)]

        self.viewer = None
        self._n_agents_routes = None
        self.full_observable = full_observable

        # agent id (n_agents, onehot), pos (2)
        self._obs_high = np.array([1.0] * self.n_agents + [1.0, 1.0])
        self._obs_low = np.array([0.0] * self.n_agents + [0.0, 0.0])
        if self.full_observable:
            self._obs_high = np.tile(self._obs_high, self.n_agents)
            self._obs_low = np.tile(self._obs_low, self.n_agents)
        self.observation_space = MultiAgentObservationSpace([spaces.Box(self._obs_low, self._obs_high) for _ in range(self.n_agents)])

        self.agents_colors = [(random.randrange(256), random.randrange(256), random.randrange(256)) for _ in range(self.n_agents)]

    def action_space_sample(self):
        return [agent_action_space.sample() for agent_action_space in self.action_space]

    def __init_full_obs(self):
        self._full_obs = self.__create_grid()

        for agent_i in range(self.n_agents):
            while True:
                #pos = [random.randint(0, self._grid_shape[0] - 1), random.randint(0, self._grid_shape[1] - 1)]
                pos = random.choice(list(self._route_vectors.keys()))
                self._agents_direction[agent_i] = self._route_vectors[pos]
                if self._is_cell_vacant(pos):
                    self.agent_pos[agent_i] = pos
                    self.curr_cars_count += 1
                    self.on_the_road[agent_i] = True
                    self._agents_routes[agent_i] = random.randint(1, 3)
                    self.__update_agent_view(agent_i)
                    break
                elif self.curr_cars_count >= 4:
                    self.agent_pos[agent_i] = (0, 0)  # not yet on the road
                    self.__update_agent_view(agent_i)
                    break
            

        self.__draw_base_img()

    def __create_grid(self):
        _grid = [[PRE_IDS['empty'] for _ in range(self._grid_shape[1])] for row in range(self._grid_shape[0])]
        return _grid

    def _is_cell_vacant(self, pos):
        return self.is_valid(pos) and (self._full_obs[pos[0]][pos[1]] == PRE_IDS['empty'])

    def is_valid(self, pos):
        return (0 <= pos[0] < self._grid_shape[0]) and (0 <= pos[1] < self._grid_shape[1])

    def __update_agent_view(self, agent_i):
        self._full_obs[self.agent_pos[agent_i][0]][self.agent_pos[agent_i][1]] = PRE_IDS['agent'] + str(agent_i + 1)

    def __check_colision(self, pos):
        return self.is_valid(pos) and (self._full_obs[pos[0]][pos[1]].find(PRE_IDS['agent']) > -1)

    def __is_gate_free(self):
        for pos in self._entry_gates:
            if pos not in self.agent_pos.values():
                return True
        return False

    def __reached_dest(self, agent_i):
        pos = self.agent_pos[agent_i]
        if pos in self._destination:
            self._full_obs[pos[0]][pos[1]] = PRE_IDS['empty']
            return True
        return False



    def get_agent_obs(self):
        # obs_agent_i size is 21: |id|=10(n_agents) + |mask|=9(3x3) + |coords|=2(x, y) 
        _obs = []
        for agent_i in range(self.n_agents):
            pos = self.agent_pos[agent_i]

            # agent id
            _agent_i_obs = [0 for _ in range(self.n_agents)]
            _agent_i_obs[agent_i] = 1


            # gets other agents position relative to agent_i position, 3x3 view mask
            _other_agents_pos = np.zeros(self._agent_view_mask)  # other agents location in neighbour
            
            for row in range(max(0, pos[0] - 1), min(pos[0] + 1 + 1, self._grid_shape[0])):
                for col in range(max(0, pos[1] - 1), min(pos[1] + 1 + 1, self._grid_shape[1])):
                    if PRE_IDS['agent'] in self._full_obs[row][col]:
                        _other_agents_pos[row - (pos[0] - 1), col - (pos[1] - 1)] = 1  # get relative position for other agents loc.

            _other_agents_pos[1, 1] = 0.0  # set center value to 0 that belongs to agent_i TODO: see if this stays or not

            _agent_i_obs += _other_agents_pos.flatten().tolist()  # adding other agents pos in observable area


            # location
            _agent_i_obs += [pos[0] / self._grid_shape[0], pos[1] / (self._grid_shape[1] - 1)]  # coordinates

            _obs.append(_agent_i_obs)

        if self.full_observable:
            _obs = np.array(_obs).flatten().tolist()
            _obs = [_obs for _ in range(self.n_agents)]
        return _obs

    def __draw_base_img(self):
        # create grid and make everything black
        img = draw_grid(self._grid_shape[0], self._grid_shape[1], cell_size=CELL_SIZE, fill=WALL_COLOR)

        # draw tracks
        for i, row in enumerate(self._full_obs):
            for j, col in enumerate(row):
                if col == PRE_IDS['empty']:
                    fill_cell(img, (i, j), cell_size=CELL_SIZE, fill='white', margin=0.05)
        return img

    def __create_grid(self):
        # create a grid with every cell as wall
        _grid = [[PRE_IDS['wall'] for _ in range(self._grid_shape[1])] for row in range(self._grid_shape[0])]

        # draw track by making cells empty :
        # horizontal tracks
        _grid[self._grid_shape[0] // 2 - 1] = [PRE_IDS['empty'] for _ in range(self._grid_shape[1])]
        _grid[self._grid_shape[0] // 2] = [PRE_IDS['empty'] for _ in range(self._grid_shape[1])]

        # vertical tracks
        for row in range(self._grid_shape[0]):
            _grid[row][self._grid_shape[1] // 2 - 1] = PRE_IDS['empty']
            _grid[row][self._grid_shape[1] // 2] = PRE_IDS['empty']

        return _grid

    def step(self, agents_action):
        assert len(agents_action) == self.n_agents

        self._step_count += 1
        rewards = [self._step_cost for _ in range(self.n_agents)]

        for agent_i, action in enumerate(agents_action):
            if not (self._agent_dones[agent_i]) and self.on_the_road[agent_i]:
                col_reward = self.__update_agent_pos(agent_i, action)
                rewards[agent_i] += col_reward

        #for gate_i in range(self._n_gates):
        # TODO remove self.curr_cars_count < self._n_max here, might not be needed if we play with the on the road
        if random.uniform(0, 1) < self._arrive_prob and self.curr_cars_count < self._n_max:
            for agent_i in range(self.n_agents):
                if self.__is_gate_free() and not self.on_the_road[agent_i]:
                    while True:
                        pos = random.choice(list(self._route_vectors.keys()))
                        self._agents_direction[agent_i] = self._route_vectors[pos]
                        if self._is_cell_vacant(pos):
                            self.agent_pos[agent_i] = pos
                            self.curr_cars_count += 1
                            self.on_the_road[agent_i] = True
                            self._agent_turned[agent_i] = False
                            self._agents_routes[agent_i] = random.randint(1, 3)
                            self.__update_agent_view(agent_i)
                            break
                    

        for agent_i in range(self.n_agents):
            if self.__reached_dest(agent_i):
                self.on_the_road[agent_i] = False
                self.curr_cars_count -= 1

        if self._step_count >= self._max_steps:
            for i in range(self.n_agents):
                self._agent_dones[i] = True

        for agent_i in range(self.n_agents):
            if self.on_the_road[agent_i]:
                rewards[agent_i] += - 0.01 * self._step_count
            self._total_episode_reward[agent_i] += rewards[agent_i]


        return self.get_agent_obs(), rewards, self._agent_dones, None


    def __get_next_move(self, move, route, curr_pos, agent_i):
        dir_vector = self._agents_direction[agent_i]

        if route == 1:
            next_pos = tuple([curr_pos[i] + self._agents_direction[agent_i][i] for i in range(len(curr_pos))])
        else:
            sig = (1 if dir_vector[1] != 0 else -1) if route == 2 else (-1 if dir_vector[1] != 0 else 1) 
            new_dir_vector = (dir_vector[1] * sig, 0) if dir_vector[0] == 0 else (0, dir_vector[0] * sig)

        return new_dir_vector




    def __update_agent_pos(self, agent_i, move):

        curr_pos = copy.copy(self.agent_pos[agent_i])
        next_pos = None
        route = self._agents_routes[agent_i]

        if move == 0:  # GAS
            if route == 1:
                next_pos = tuple([curr_pos[i] + self._agents_direction[agent_i][i] for i in range(len(curr_pos))])
            else:
                turn_pos = self._turning_places[self._agents_direction[agent_i]]
                if curr_pos == turn_pos[route - 2] and not self._agent_turned[agent_i]:
                    new_dir_vector = self.__get_next_move(move, route, curr_pos, agent_i)
                    self._agents_direction[agent_i] = new_dir_vector
                    self._agent_turned[agent_i] = True
                    next_pos = tuple([curr_pos[i] + new_dir_vector[i] for i in range(len(curr_pos))])
                else:
                    next_pos = tuple([curr_pos[i] + self._agents_direction[agent_i][i] for i in range(len(curr_pos))])
                #dir_vector = self._agents_direction[agent_i]
                #turn_pos = (self._grid_shape[0] / 2)
        elif move == 1:  # BRAKE
            pass
        else:
            raise Exception('Action Not found!')

        self.__get_next_move(1, 3, curr_pos, agent_i)
        if next_pos is not None and self.__check_colision(next_pos):
            return self._collision_reward

        if next_pos is not None and self._is_cell_vacant(next_pos):
            self.agent_pos[agent_i] = next_pos
            self._full_obs[curr_pos[0]][curr_pos[1]] = PRE_IDS['empty']
            self.__update_agent_view(agent_i)

        return 0

    def reset(self):
        self._total_episode_reward = [0 for _ in range(self.n_agents)]
        self._step_count = 0
        self._agent_dones = [False for _ in range(self.n_agents)]
        self.on_the_road = [False for _ in range(self.n_agents)]
        self._agent_turned = [False for _ in range(self.n_agents)]
        self.curr_cars_count = 0
        
        self.agent_pos = {}
        self.__init_full_obs()

        #self._full_obs = self.__create_grid()

        # sample cars for each location
        for gates in self._entry_gates:
            if self.curr_cars_count <= self._n_max and random.random() < self._arrive_prob:
                pass

        return self.get_agent_obs()

    def render(self, mode='human'):
        img = copy.copy(self._base_img)

        for agent_i in range(self.n_agents):
            if self.on_the_road[agent_i]:
                fill_cell(img, self.agent_pos[agent_i], cell_size=CELL_SIZE, fill=self.agents_colors[agent_i])
                write_cell_text(img, text=str(agent_i + 1), pos=self.agent_pos[agent_i], cell_size=CELL_SIZE,
                                fill='white', margin=0.3)
            

        img = np.asarray(img)
        if mode == 'rgb_array':
            return img
        elif mode == 'human':
            from gym.envs.classic_control import rendering
            if self.viewer is None:
                self.viewer = rendering.SimpleImageViewer()
            self.viewer.imshow(img)
            return self.viewer.isopen

    def seed(self, n):
        self.np_random, seed1 = seeding.np_random(n)
        seed2 = seeding.hash_seed(seed1 + 1) % 2 ** 31
        return [seed1, seed2]

    def close(self):
        if self.viewer is not None:
            self.viewer.close()
            self.viewer = None


CELL_SIZE = 30

WALL_COLOR = 'black'

ACTION_MEANING = {
    0: "GAS",
    1: "BRAKE",
}

PRE_IDS = {
    'wall': 'W',
    'empty': '0',
    'agent': 'A'
}

AGENT_COLORS = {
    0: 'red',
    1: 'blue',
    2: 'green',
    3: 'yellow',
    4: 'pink',
    5: 'brown'
}
