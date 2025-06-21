import pygame
import math
import random

def get_time_factor(pos, time_center, max_radius, exponent=2.3):
    dist = (pos - time_center).length()
    t = min(dist / max_radius, 1.0)
    return (1.0 - t) ** exponent

class GameWorld:
    def __init__(self):
        self.timers = []
        self.entities = []
        self.global_commands = []
        self.last_global_time = 0.0
        self.global_time = 0.0
        self.rewinding = False
        self.permanent_command_log = []
        self.current_timeline_id = 0
        self.next_timeline_id = 1 
        
    def update(self, dt):
        if self.rewinding:
            self.global_time = max(0.0, self.global_time - dt)
        else:
            self.global_time += dt

        # Clamp last_global_time to 0 if needed
        if self.global_time == 0.0:
            self.last_global_time = 0.0

        forward_progress = not self.rewinding and self.global_time > self.last_global_time

        for timer in self.timers:
            timer.update(dt, self.global_time, self.rewinding)

        # Global commands (if any in future)
        for cmd in self.global_commands:
            if isinstance(cmd, Command):
                if self.rewinding and cmd.executed and self.global_time < cmd.scheduled_time:
                    cmd.reverse()
                elif forward_progress and not cmd.executed and self.global_time >= cmd.scheduled_time:
                    cmd.execute()

        self.last_global_time = self.global_time

class Command:
    def __init__(self, target, data, forward_fn, backward_fn, scheduled_time):
        self.target = target
        self.data = data
        self.forward_fn = forward_fn
        self.backward_fn = backward_fn
        self.scheduled_time = scheduled_time
        self.executed = False

    def execute(self):
        if not self.executed:
            self.forward_fn(self.target, self.data)
            self.executed = True

    def reverse(self):
        if self.executed:
            self.backward_fn(self.target, self.data)
            self.executed = False

class TimeEntity:
    def __init__(self, pos, color=(255, 255, 255), max_lifetime=float('inf')    ):
        self.pos = pygame.Vector2(pos)
        self.color = color
        self.local_time = 0.0
        self.command_queue = []
        self.command_index = 0
        self.max_lifetime = max_lifetime

        self.record_initial_state()

    @property
    def dead(self):
        # Only dead if outside valid time window
        return self.local_time < 0 or (
            hasattr(self, 'max_lifetime') and self.local_time >= self.max_lifetime
        )

    def update(self, dt, global_time, rewinding=False):
        time_factor = get_time_factor(self.pos, pygame.Vector2(400, 300), 500)
        local_dt = -dt if rewinding else dt
        self.local_time += local_dt * time_factor

        self.local_time = max(self.local_time, 0.0)

        while self.command_index < len(self.command_queue):
            cmd = self.command_queue[self.command_index]
            if not rewinding and self.local_time >= cmd.scheduled_time:
                cmd.execute()
                self.command_index += 1
            else:
                break

        while self.command_index > 0:
            cmd = self.command_queue[self.command_index - 1]
            if rewinding and self.local_time < cmd.scheduled_time:
                self.command_index -= 1
                cmd.reverse()
            else:
                break

    def record_initial_state(self):
        self._initial_pos = self.pos.copy()

    def reset_to_initial(self):
        self.pos = self._initial_pos.copy()
        self.command_index = 0
        self.local_time = 0
        self.command_queue = []

    def queue_command(self, cmd):
        self.command_queue.append(cmd)

class Timer(TimeEntity):
    def __init__(self, pos):
        super().__init__(pos, color=(255, 255, 0))
        self.count = 0.0

    def update(self, dt, global_time, rewinding=False):
        prev_local_time = self.local_time
        super().update(dt, global_time, rewinding)
        # Update count based on local_time
        self.count = max(self.local_time, 0.0)  # Or round if you want .00

class MovementComponent:
    def __init__(self):
        self.path = []  # List of (scheduled_time, pos) tuples, sorted by time

    def add_step(self, scheduled_time, pos):
        self.path.append((scheduled_time, pos))

    def ensure_path(self, until_time, step_size, start_pos, velocity):
        # Fill path up to until_time, starting from last or start_pos
        last_time = self.path[-1][0] if self.path else 0
        last_pos = self.path[-1][1] if self.path else start_pos
        t = last_time
        while t < until_time:
            t += step_size
            time_factor = get_time_factor(last_pos, pygame.Vector2(400, 300), 500)
            last_pos = last_pos + velocity * step_size * time_factor
            self.path.append((t, last_pos.copy()))

    def get_pos(self, query_time):
        if not self.path:
            return None
        prev_time, prev_pos = self.path[0]
        for next_time, next_pos in self.path[1:]:
            if query_time <= next_time:
                if next_time > prev_time:
                    alpha = (query_time - prev_time) / (next_time - prev_time)
                else:
                    alpha = 0
                alpha = max(0.0, min(1.0, alpha))  # Clamp to [0, 1]
                return prev_pos.lerp(next_pos, alpha)
            prev_time, prev_pos = next_time, next_pos
        return self.path[-1][1]