import pygame
import math
import random

pygame.init()
screen = pygame.display.set_mode((1000, 600))
clock = pygame.time.Clock()

CENTER = pygame.Vector2(500, 300)
time_center = CENTER.copy()

MAX_RADIUS = 400
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (255, 100, 100)
GREY = (100, 100, 100)

NUM_ENTITIES = 10
ENTITY_SPACING = 40
COMMAND_INTERVAL = 0.4
NUM_COMMANDS = 50

show_overlay = True
overlay_ripple = 0.0
overlay_ripple_decay = 3.0

def get_time_factor(pos, time_center, max_radius):
    dist = (pygame.Vector2(pos) - time_center).length()
    t = min(dist / max_radius, 1.0)
    return max(0.001, 1.0 - t**2)

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
            self.forward_fn(self.data, self.target)
            self.executed = True

    def reverse(self):
        if self.executed:
            self.backward_fn(self.data, self.target)
            self.executed = False  # ← This is crucial!


class SpatialTimer:
    def __init__(self, pos):
        self.pos = pygame.Vector2(pos)
        self.local_time = 0.0

    def update(self, dt, rewinding):
        tf = get_time_factor(self.pos, time_center, MAX_RADIUS)
        delta = -dt if rewinding else dt
        self.local_time = max(0.0, self.local_time + delta * tf)

    def draw(self, surface, font):
        # Format time to 1 decimal place
        time_str = f"{self.local_time:.1f}s"
        text = font.render(time_str, True, WHITE)
        text_rect = text.get_rect(center=self.pos)
        surface.blit(text, text_rect)


class TimeEntity:
    def __init__(self, pos, color):
        self.initial_pos = pygame.Vector2(pos)
        self.logical_pos = pygame.Vector2(pos)  # ← Track this separately
        self.pos = pygame.Vector2(pos)
        self.local_time = 0.0
        self.command_queue = []
        self.command_index = 0
        self.color = color

    def update(self, dt, global_time, rewinding=False):
        # Compute time factor and adjust local time
        tf = get_time_factor(self.pos, time_center, MAX_RADIUS)
        delta = -dt if rewinding else dt
        self.local_time = max(0.0, self.local_time + delta * tf)

        # Execute forward in time
        while self.command_index < len(self.command_queue):
            cmd = self.command_queue[self.command_index]
            if self.local_time >= cmd.scheduled_time:
                cmd.execute()
                self.command_index += 1
            else:
                break

        # Reverse if rewinding
        while self.command_index > 0:
            prev_cmd = self.command_queue[self.command_index - 1]
            if self.local_time < prev_cmd.scheduled_time:
                prev_cmd.reverse()
                self.command_index -= 1
            else:
                break

    def draw(self, surface):
        pygame.draw.circle(surface, self.color, self.pos, 8)

    def draw_ghosts(self, surface, global_time):
        for cmd in self.command_queue:
            if not cmd.executed:
                pos = cmd.data['new_pos']
                ghost = pygame.Surface((16, 16), pygame.SRCALPHA)
                age = max(0.01, cmd.scheduled_time - self.local_time)
                alpha = max(20, min(180, int(255 * (1.0 - age / 5.0))))
                pygame.draw.circle(ghost, (150, 150, 150, alpha), (8, 8), 6)
                surface.blit(ghost, (pos.x - 8, pos.y - 8))

class Bullet(TimeEntity):
    def __init__(self, pos, velocity, color=(0, 200, 255)):
        super().__init__(pos, color)
        self.velocity = velocity
        self.dead = False
        self.age = 0.0
        self.max_age = 3.0  # seconds

    def update(self, dt, global_time, rewinding=False):
        super().update(dt, global_time, rewinding)

        # Calculate how fast this bullet experiences time
        time_factor = get_time_factor(self.pos, time_center, MAX_RADIUS)
        local_dt = dt * time_factor

        if rewinding:
            self.age -= local_dt
            if self.age < self.max_age:
                self.dead = False  # Revive if rewinding brings it back before expiration
        else:
            self.age += local_dt
            if self.age > self.max_age:
                self.dead = True

        MAX_COMMANDS = 200
        if len(self.command_queue) > MAX_COMMANDS:
            self.command_queue.pop(0)
            self.command_index = max(0, self.command_index - 1)

        if not self.dead and len(self.command_queue) - self.command_index < 5:
            prev = self.pos.copy()
            for i in range(5):
                t = self.local_time + i * 0.05
                new = prev + self.velocity * 0.05

                cmd = Command(
                    target=self,
                    data={"prev_pos": prev, "new_pos": new},
                    forward_fn=move_forward,
                    backward_fn=move_backward,
                    scheduled_time=t
                )
                self.command_queue.append(cmd)
                prev = new

class SpawnBulletCommand(Command):
    def __init__(self, world, bullet_data, scheduled_time):
        self.world = world
        self.bullet_data = bullet_data
        self.scheduled_time = scheduled_time
        self.executed = False
        self.bullet = None

    def execute(self):
        if not self.executed:
            bullet = Bullet(self.bullet_data["pos"], self.bullet_data["velocity"], self.bullet_data["color"])
            self.bullet = bullet
            self.world.bullets.append(bullet)
            if hasattr(self.world, "time_entities"):
                self.world.time_entities.append(bullet)  # ← make sure to track bullets here too
            self.executed = True

    def reverse(self):
        print(f"Reversing bullet spawn at t={self.scheduled_time}")
        if self.executed and self.bullet in self.world.bullets:
            self.world.bullets.remove(self.bullet)
            if hasattr(self.world, "time_entities") and self.bullet in self.world.time_entities:
                self.world.time_entities.remove(self.bullet)
            self.bullet.command_queue.clear()
            self.bullet.dead = True
            self.executed = False

class BuddyShootCommand(Command):
    def __init__(self, world, buddy, scheduled_time):
        self.world = world
        self.buddy = buddy
        self.spawn_cmd = None
        self.scheduled_time = scheduled_time
        self.executed = False
        self.target = buddy
        self.data = {}

        # Freeze all randomness now to ensure determinism
        angle = random.uniform(0, 2 * math.pi)
        speed = 150
        velocity = pygame.Vector2(math.cos(angle), math.sin(angle)) * speed
        pos = buddy.pos.copy()

        self.bullet_data = {
            "pos": pos,
            "velocity": velocity,
            "color": (255, 200, 50)
        }

    def execute(self):
        if not self.executed and self.buddy in self.world.buddies:
            spawn_time = self.scheduled_time + 0.01
            self.spawn_cmd = SpawnBulletCommand(self.world, self.bullet_data, spawn_time)
            self.world.global_commands.append(self.spawn_cmd)
            self.executed = True

    def reverse(self):
        print(f"Reverse BuddyShootCommand at t={self.scheduled_time}")
        if self.spawn_cmd:
            self.spawn_cmd.reverse()
            if self.spawn_cmd in self.world.global_commands:
                self.world.global_commands.remove(self.spawn_cmd)
        self.executed = False

def make_movement_command(entity, offset, scheduled_time):
    prev = entity.logical_pos.copy()
    new = prev + offset
    entity.logical_pos = new.copy()  # ← Update logical position now
    return Command(entity, {'prev_pos': prev, 'new_pos': new}, move_forward, move_backward, scheduled_time)

def move_forward(data, entity):
    entity.pos = data['new_pos']

def move_backward(data, entity):
    entity.pos = data['prev_pos']

def draw_time_gradient_overlay(surface, time_center, max_radius, ripple_strength=0.0):
    overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
    width, height = surface.get_size()
    grid_size = 30

    for y in range(0, height, grid_size):
        for x in range(0, width, grid_size):
            pos = pygame.Vector2(x + grid_size / 2, y + grid_size / 2)
            tf = get_time_factor(pos, time_center, max_radius)

            # Color hue from blue (fast) to red (slow)
            hue = int((1 - tf) * 255)
            color = (hue, int(tf * 255), 255 - hue)

            # Add ripple alpha effect
            dist = (pos - time_center).length()
            ripple_alpha = int(30 + 50 * math.sin(dist * 0.05 - ripple_strength * 5)) if ripple_strength > 0 else 40

            r = max(0, min(255, int(color[0])))
            g = max(0, min(255, int(color[1])))
            b = max(0, min(255, int(color[2])))
            a = max(0, min(255, int(ripple_alpha)))

            pygame.draw.rect(
                overlay,
                (r, g, b, a),
                (x, y, grid_size, grid_size)
            )

            # Draw flow direction vector
            flow_length = tf * 8
            direction = (pos - time_center).normalize() * flow_length
            flow_start = pos
            flow_end = pos + direction
            flow_color = (255, 255, 255, int(80 + tf * 100))  # brighter near center

            pygame.draw.line(overlay, flow_color, flow_start, flow_end, 2)


    surface.blit(overlay, (0, 0))

NUM_BUDDIES = 5
buddies = []

for _ in range(NUM_BUDDIES):
    x = random.randint(200, 800)
    y = random.randint(100, 500)
    buddy = TimeEntity((x, y), (0, 255, 100))
    buddies.append(buddy)

class GameWorld:
    def __init__(self):
        self.buddies = []
        self.bullets = []
        self.global_commands = []
        self.last_global_time = 0.0

    def update(self, dt, global_time, rewinding):
        for entity in self.buddies:
            entity.update(dt, global_time, rewinding)

        for bullet in self.bullets:
            bullet.update(dt, global_time, rewinding)

        self.bullets[:] = [b for b in self.bullets if not b.dead]

        forward_progress = not rewinding and global_time > self.last_global_time

        for cmd in self.global_commands:
            if isinstance(cmd, Command):
                if rewinding:
                    if not cmd.executed:
                        print(f"Skipping: {cmd} — not executed yet")
                    elif global_time >= cmd.scheduled_time:
                        print(f"Skipping: {cmd} — global time too high ({global_time:.2f} >= {cmd.scheduled_time:.2f})")
                    else:
                        print(f"Should reverse: {cmd}")
                        cmd.reverse()
                else:
                    # For BuddyShootCommand, compare using buddy's local_time
                    if not cmd.executed:
                        time_check = cmd.scheduled_time
                        if isinstance(cmd, BuddyShootCommand):
                            if cmd.buddy.local_time >= time_check and forward_progress:
                                cmd.execute()
                        else:
                            if global_time >= time_check and forward_progress:
                                cmd.execute()


        self.last_global_time = global_time

def spawn_random_buddies(world, count):
    for _ in range(count):
        x = random.randint(100, 700)
        y = random.randint(100, 500)
        color = (random.randint(100, 255), random.randint(100, 255), random.randint(100, 255))
        buddy = TimeEntity(pygame.Vector2(x, y), color=color)
        world.buddies.append(buddy)

def schedule_buddy_shooting(world, duration=30.0, interval=0.5):
    for buddy in world.buddies:
        t = 0.0
        while t < duration:
            shoot_cmd = BuddyShootCommand(world, buddy, scheduled_time=t)
            world.global_commands.append(shoot_cmd)
            t += interval

world = GameWorld()
spawn_random_buddies(world, 3)
schedule_buddy_shooting(world, duration=30.0, interval=0.75)

def schedule_bullet_shot(buddy, start_time):
    cmd = BuddyShootCommand(world, buddy, scheduled_time=start_time)
    world.global_commands.append(cmd)

font = pygame.font.SysFont("consolas", 16)
timers = []

# Along x-axis
for i in range(-5, 6):
    offset = i * 100  # More space between timers
    timers.append(SpatialTimer((CENTER.x + offset, CENTER.y)))

# Along y-axis
for i in range(-3, 4):
    offset = i * 100
    timers.append(SpatialTimer((CENTER.x, CENTER.y + offset)))

running = True
global_time = 0.0
rewinding = False
time_since_last_bullet = 0.0
while running:
    dt = clock.tick(60) / 1000
    if rewinding:
        global_time -= dt
    else:
        global_time += dt


    if overlay_ripple > 0:
        overlay_ripple -= dt * overlay_ripple_decay
        overlay_ripple = max(0, overlay_ripple)

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_r:
                rewinding = not rewinding
                overlay_ripple = 1.0  # trigger pulse
            if event.key == pygame.K_h:
                show_overlay = not show_overlay

    keys = pygame.key.get_pressed()
    if keys[pygame.K_LEFT]:
        time_center.x -= 200 * dt
    if keys[pygame.K_RIGHT]:
        time_center.x += 200 * dt
    if keys[pygame.K_UP]:
        time_center.y -= 200 * dt
    if keys[pygame.K_DOWN]:
        time_center.y += 200 * dt

    for timer in timers:
        timer.update(dt, rewinding)

    world.update(dt, global_time, rewinding)

    # Draw time field
    screen.fill(BLACK)

    if show_overlay:
        draw_time_gradient_overlay(screen, time_center, MAX_RADIUS, ripple_strength=overlay_ripple)

    pygame.draw.circle(screen, (40, 40, 80), time_center, MAX_RADIUS, 2)

    for entity in world.buddies:
        entity.draw_ghosts(screen, global_time)
        entity.draw(screen)

    for bullet in world.bullets:
        bullet.draw_ghosts(screen, global_time)
        bullet.draw(screen)


    pygame.display.flip()

pygame.quit()
