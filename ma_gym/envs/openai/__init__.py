import gym
from ..utils.action_space import MultiAgentActionSpace


class MultiAgentWrapper(gym.Wrapper):
    """ It's a multi agent wrapper over openai's single agent environments. """

    def __init__(self, name):
        super().__init__(gym.make(name))
        self.n_agents = 1
        self.action_space = MultiAgentActionSpace([self.env.action_space])

    def step(self, action_n):
        assert len(action_n) == self.n_agents

        action = action_n[0]
        obs, reward, done, info = self.env.step(action)

        # Following is a hack:
        # If this is not done and there is a there max step overflow then the TimeLimit Wrapper handles it and
        # makes done = True rather than making it a list of boolean values.
        # Nicer Options : Re-write Env Registry to have custom TimeLimit Wrapper for Multi agent envs
        # Or, we can simply pass a boolean value ourselves rather than a list
        if self.env._elapsed_steps == (self.env._max_episode_steps - 1):
            done = True

        return [obs], [reward], [done], info

    def reset(self):
        obs = self.env.reset()
        return [obs]