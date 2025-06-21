import pygame
import sys
import math
import random
from collections import defaultdict
from time_travel import *

pygame.init()
screen = pygame.display.set_mode((800, 600))
clock = pygame.time.Clock()
font = pygame.font.SysFont("consolas", 16)

TIMELINE_COLORS = [
    (255, 80, 80),   # Bright Red
    (60, 190, 255),  # Electric Blue
    (80, 255, 80),   # Bright Green
    (255, 255, 80),  # Bright Yellow
    (200, 80, 255),  # Magenta
    (255, 170, 60),  # Orange
    (60, 255, 190),  # Bright Teal
]

MAX_REWINDS = 3
rewind_charges = MAX_REWINDS
active_timelines = defaultdict(int)
timeline_counter = 0

world = GameWorld()

class Bullet(TimeEntity):
    def __init__(self, pos, velocity, bullet_id, max_lifetime=1.4):
        super().__init__(pos, color=(255, 200, 50), max_lifetime=max_lifetime)
        self.velocity = velocity
        self.bullet_id = bullet_id
        self.movement = MovementComponent()
        self.movement.ensure_path(self.max_lifetime, 0.05, self.pos.copy(), self.velocity)

    def update(self, dt, global_time, rewinding=False):
        super().update(dt, global_time, rewinding)
        self.pos = self.movement.get_pos(self.local_time)

class GhostBullet(Bullet):
    def __init__(self, spawn_time, pos, velocity, global_time, max_lifetime=2.5, color=(200, 200, 200), timeline_id=0):
        super().__init__(pos, velocity, bullet_id=-1, max_lifetime=max_lifetime)
        self.spawn_time = spawn_time
        self.ghost = True
        self.color = color
        self.timeline_id = timeline_id
        self._update_pos(global_time)

    def _update_pos(self, global_time):
        self.local_time = max(0, global_time - self.spawn_time)
        self.pos = self.movement.get_pos(self.local_time)

    def update(self, dt, global_time, rewinding=False):
        if global_time < self.spawn_time:
            # Not yet born
            return
        self._update_pos(global_time)

def prune_future_bullet_spawns(world, time_point, new_timeline_id, just_overwritten_timeline_id):
    ghosts_created = False
    for cmd in world.permanent_command_log:
        if hasattr(cmd, "forward_fn") and getattr(cmd.forward_fn, "__name__", "") == "shoot_bullet":
            if (
                cmd.origin_timeline == just_overwritten_timeline_id and
                cmd.scheduled_time > time_point and
                new_timeline_id not in cmd.ghosted_timelines
            ):
                # spawn ghost as before...
                cmd.ghosted_timelines.add(new_timeline_id)
                data = cmd.data
                pos = data["pos"].copy()
                velocity = data["velocity"]
                max_lifetime = data.get("max_lifetime", 2.5)
                color = TIMELINE_COLORS[new_timeline_id % len(TIMELINE_COLORS)]
                ghost = GhostBullet(cmd.scheduled_time, pos, velocity, world.global_time, max_lifetime, color=color, timeline_id=new_timeline_id)
                world.entities.append(ghost)
                active_timelines[new_timeline_id] += 1
                ghosts_created = True
                cmd.ghosted_timelines.add(new_timeline_id)
                if cmd in world.global_commands:
                    world.global_commands.remove(cmd)
    return ghosts_created

def shoot_bullet(world, data):
    bullet = Bullet(data["pos"], data["velocity"], data["bullet_id"])
    world.entities.append(bullet)

def undo_bullet(world, data):
    bullet_id = data.get("bullet_id")
    for b in list(world.entities):
        if isinstance(b, Bullet) and getattr(b, "bullet_id", None) == bullet_id:
            world.entities.remove(b)
            break

class GhostPlayer(TimeEntity):
    def __init__(self, movement_path, color=(180, 180, 255), timeline_id=0):
        super().__init__(movement_path[0][1], color=color)
        self.movement = MovementComponent()
        self.movement.path = movement_path
        self.ghost = True
        self.timeline_id = timeline_id

    def update(self, dt, global_time, rewinding=False):
        # Always use world/global time for ghosts (not pruned/overwritten)
        self.local_time = global_time
        self.pos = self.movement.get_pos(self.local_time)

class Player(TimeEntity):
    def __init__(self, pos, move_speed=170):
        super().__init__(pos, color=(80, 220, 100))
        self.shoot_cooldown = 0.15  # seconds between shots
        self.last_shot_time = 0
        self.move_speed = move_speed
        self.movement = MovementComponent()
        self.movement.path = [(0.0, pygame.Vector2(pos))]

    def update(self, dt, global_time, rewinding=False):
        self.pos = self.movement.get_pos(self.local_time)
        super().update(dt, global_time, rewinding)

    def can_shoot(self):
        return (self.local_time - self.last_shot_time) >= self.shoot_cooldown

    def shoot(self, target_pos, world):
        angle = math.atan2(target_pos[1] - self.pos.y, target_pos[0] - self.pos.x)
        speed = 250
        velocity = pygame.Vector2(math.cos(angle), math.sin(angle)) * speed
        bullet_id = random.randint(1, 1_000_000_000)
        bullet_data = {
            "pos": self.pos.copy(),
            "velocity": velocity,
            "bullet_id": bullet_id
        }
        cmd = Command(
            target=world,
            data=bullet_data,
            forward_fn=shoot_bullet,
            backward_fn=undo_bullet,
            scheduled_time=self.local_time + 0.01
        )
        cmd.origin_timeline = world.current_timeline_id  # set to current timeline
        cmd.ghosted_timelines = set()
        world.global_commands.append(cmd)
        world.permanent_command_log.append(cmd)
        cmd.execute()
        self.last_shot_time = self.local_time

def prune_future_player_moves(world, time_point, new_timeline_id, just_overwritten_timeline_id):
    ghosts_created = False
    # Collect all player move commands to be ghosted as a path
    ghost_path = []
    for cmd in world.permanent_command_log:
        if getattr(cmd, "type", None) == "player_move":
            if (
                cmd.origin_timeline == just_overwritten_timeline_id and
                cmd.scheduled_time > time_point and
                new_timeline_id not in cmd.ghosted_timelines
            ):
                ghost_path.append((cmd.scheduled_time, cmd.data["pos"].copy()))
                cmd.ghosted_timelines.add(new_timeline_id)
    if ghost_path:
        color = TIMELINE_COLORS[new_timeline_id % len(TIMELINE_COLORS)]
        ghost = GhostPlayer(ghost_path, color=color, timeline_id=new_timeline_id)
        world.entities.append(ghost)
        active_timelines[new_timeline_id] += 1
        ghosts_created = True
    return ghosts_created

player = Player(pygame.Vector2(400, 300))
world.player = player
world.entities.append(player)

# Add timers to the world in a grid
for x in range(100, 701, 100):
    for y in range(100, 501, 100):
        timer = Timer((x, y))
        world.entities.append(timer)

was_rewinding_last_frame = False

# Game loop
running = True
while running:
    dt = clock.tick(144) / 1000.0

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_r:
                if not world.rewinding and rewind_charges > 0:
                    world.rewinding = True
                elif world.rewinding:
                    world.rewinding = False
        elif event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 1:  # Left click
                mouse_pos = pygame.mouse.get_pos()
                if player.can_shoot():
                    player.shoot(mouse_pos, world)

    keys = pygame.key.get_pressed()
    move_dir = pygame.Vector2(0, 0)
    if keys[pygame.K_w]: move_dir.y -= 1
    if keys[pygame.K_s]: move_dir.y += 1
    if keys[pygame.K_a]: move_dir.x -= 1
    if keys[pygame.K_d]: move_dir.x += 1

    if move_dir.length_squared() > 0:
        move_dir = move_dir.normalize()
        # Calculate where the player should be next frame
        step_size = dt  # Could also use a fixed step, e.g., 0.01
        velocity = move_dir * player.move_speed
        # Get current simulated position
        curr_pos = player.movement.get_pos(player.local_time) or player.pos
        next_pos = curr_pos + velocity * step_size
        # Add this step to the movement component, at local_time + step_size
        player.movement.add_step(player.local_time + step_size, next_pos)

        move_cmd = Command(
            target=world,
            data={
                "entity_type": "player",
                "pos": next_pos.copy(),
                "scheduled_time": player.local_time + step_size,
                "timeline_id": world.current_timeline_id
            },
            forward_fn=None,   # (You might not need to "execute" anything immediately)
            backward_fn=None,  # (Likewise)
            scheduled_time=player.local_time + step_size
        )
        move_cmd.origin_timeline = world.current_timeline_id
        move_cmd.ghosted_timelines = set()
        move_cmd.type = "player_move"
        world.permanent_command_log.append(move_cmd)

    # Update entities
    world.update(dt)

    for e in world.entities:
        e.update(dt, world.global_time, world.rewinding)

    def is_obsolete(entity):
        # Only remove if the entity hasn't been born yet (not if it's "dead" in local time!)
        if isinstance(entity, Bullet):
            return entity.local_time < 0
        return False  # For other entities, you can keep your old logic

    if not world.rewinding:
        world.entities[:] = [e for e in world.entities if not is_obsolete(e)]

    # Prune the player's future path if they just stopped rewinding
    if was_rewinding_last_frame and not world.rewinding and rewind_charges > 0:
        # Track the next available timeline id
        prev_timeline_id = world.current_timeline_id
        temp_timeline_id = world.next_timeline_id

        ghosts_created = False
        ghosts_created |= prune_future_bullet_spawns(
            world, player.local_time, 
            new_timeline_id=temp_timeline_id, 
            just_overwritten_timeline_id=prev_timeline_id
        )
        ghosts_created |= prune_future_player_moves(
            world, player.local_time,
            new_timeline_id=temp_timeline_id,
            just_overwritten_timeline_id=prev_timeline_id
        )

        if ghosts_created:
            # Actually branch
            world.current_timeline_id = temp_timeline_id
            world.next_timeline_id += 1
            rewind_charges -= 1

            world.permanent_command_log = [
                cmd for cmd in world.permanent_command_log
                if not (
                    getattr(cmd, "type", None) == "player_move" and
                    cmd.origin_timeline == world.current_timeline_id and
                    cmd.scheduled_time > player.local_time
                )
            ]

            player.command_queue = [
                cmd for cmd in player.command_queue
                if cmd.scheduled_time <= player.local_time
            ]

            player.movement.path = [
                step for step in player.movement.path
                if step[0] <= player.local_time
            ]
            
            # If path is empty, add the current position
            if not player.movement.path:
                player.movement.path = [(player.local_time, player.pos.copy())]
        else:
            # Did not branch—stay on the current timeline
            active_timelines.pop(temp_timeline_id, None)
            # Do not change current_timeline_id or next_timeline_id

    # At the end, store current rewinding state for next frame
    was_rewinding_last_frame = world.rewinding

    for entity in list(world.entities):
        if hasattr(entity, "ghost") and entity.ghost:
            end_time = None
            if isinstance(entity, GhostBullet):
                end_time = entity.spawn_time + entity.max_lifetime
            elif isinstance(entity, GhostPlayer):
                if entity.movement.path:
                    end_time = entity.movement.path[-1][0]
            if end_time is not None and world.global_time > end_time:
                world.entities.remove(entity)
                tid = getattr(entity, "timeline_id", None)
                if tid is not None:
                    active_timelines[tid] -= 1
                    if active_timelines[tid] == 0:
                        rewind_charges = min(rewind_charges + 1, MAX_REWINDS)
                        del active_timelines[tid]

    for timeline_id in range(world.next_timeline_id):  # or track all timeline_ids you've used
        # Collect all movement steps for this timeline, in time order
        path = [
            (cmd.scheduled_time, cmd.data["pos"].copy())
            for cmd in world.permanent_command_log
            if getattr(cmd, "type", None) == "player_move"
            and timeline_id in getattr(cmd, "ghosted_timelines", set())
        ]
        if not path:
            continue
        path.sort()  # Ensure correct order
        start_time, end_time = path[0][0], path[-1][0]

        if start_time <= world.global_time < end_time:
            # Deduplicate: Only one ghost per timeline
            ghost_present = any(
                isinstance(e, GhostPlayer) and getattr(e, "timeline_id", None) == timeline_id
                for e in world.entities
            )
            if not ghost_present:
                color = TIMELINE_COLORS[timeline_id % len(TIMELINE_COLORS)]
                ghost = GhostPlayer(path, color=color, timeline_id=timeline_id)
                world.entities.append(ghost)
        else:
            # Remove ghosts for this timeline if out of window
            to_remove = [e for e in world.entities if isinstance(e, GhostPlayer) and getattr(e, "timeline_id", None) == timeline_id]
            for e in to_remove:
                world.entities.remove(e)


    for cmd in world.permanent_command_log:
        if not (hasattr(cmd, "forward_fn") and getattr(cmd.forward_fn, "__name__", "") == "shoot_bullet"):
            continue
        for tid in cmd.ghosted_timelines:
            spawn = cmd.scheduled_time
            end = spawn + cmd.data.get("max_lifetime", 2.5)
            if spawn <= world.global_time < end:
                # Deduplicate by (cmd, tid)
                ghost_present = any(
                    getattr(e, "cmd_ref", None) == cmd and getattr(e, "timeline_id", None) == tid
                    for e in world.entities
                )
                if not ghost_present:
                    pos = cmd.data["pos"].copy()
                    velocity = cmd.data["velocity"]
                    color = TIMELINE_COLORS[tid % len(TIMELINE_COLORS)]
                    ghost = GhostBullet(spawn, pos, velocity, world.global_time, cmd.data.get("max_lifetime", 2.5), color=color, timeline_id=tid)
                    ghost.cmd_ref = cmd
                    world.entities.append(ghost)
            else:
                # Remove ghost if outside of lifespan
                to_remove = [e for e in world.entities if getattr(e, "cmd_ref", None) == cmd and getattr(e, "timeline_id", None) == tid]
                for e in to_remove:
                    world.entities.remove(e)


    # Draw
    screen.fill((0, 0, 0))

    # Draw time field heatmap
    for x in range(0, 800, 10):
        for y in range(0, 600, 10):
            factor = get_time_factor(pygame.Vector2(x, y), pygame.Vector2(400, 300), 500)
            intensity = int(255 * factor)
            screen.fill((intensity, 0, 0), rect=pygame.Rect(x, y, 10, 10))

    # Draw timers
    for timer in world.entities:
        if isinstance(timer, Timer):
            text = font.render(f"{timer.count:.2f}", True, (255, 255, 255))
            screen.blit(text, timer.pos + pygame.Vector2(12, -8))

    for entity in world.entities:
        if hasattr(entity, "ghost") and entity.ghost:
            spawn_time = getattr(entity, "spawn_time", None)
            if spawn_time is not None and world.global_time < spawn_time:
                continue
            color = getattr(entity, "color", (220, 220, 220))
            if isinstance(entity, GhostBullet):
                pygame.draw.circle(screen, color, (int(entity.pos.x), int(entity.pos.y)), 7, 3)
                pygame.draw.circle(screen, (255,255,255), (int(entity.pos.x), int(entity.pos.y)), 3)
            elif isinstance(entity, GhostPlayer):
                pygame.draw.circle(screen, color, (int(entity.pos.x), int(entity.pos.y)), 16, 4)
            continue

        # Normal rendering for non-ghosts
        if isinstance(entity, Bullet):
            pygame.draw.circle(screen, entity.color, (int(entity.pos.x), int(entity.pos.y)), 5)
        elif isinstance(entity, Player):
            pygame.draw.circle(screen, entity.color, (int(entity.pos.x), int(entity.pos.y)), 14)
        # etc...

            
    # Draw buddy
    pygame.draw.circle(screen, player.color, player.pos, 14)

    rewind_text = font.render(f"Rewinds: {rewind_charges}", True, (255,255,255))
    screen.blit(rewind_text, (16, 16))

    pygame.display.flip()

pygame.quit()
sys.exit()
