#!/usr/bin/env python3
"""Gold Miner — A modern terminal take on the classic Lode Runner style game.

Requirements: Python 3.6+, standard library only.
Run: python3 gold_miner.py
"""

import sys
import os
import time
import select
import tty
import termios
import shutil
import signal
import random
import math

# ============================================================================
# Constants
# ============================================================================

FPS = 60
DT = 1.0 / FPS

# Cell types
CELL_WALL = 0
CELL_DIRT = 1
CELL_DUG = 2
CELL_GOLD = 3
CELL_HOLE = 4

# Guard states
GUARD_PATROL = 0
GUARD_CHASE = 1
GUARD_TRAPPED = 2

# Game states
STATE_TITLE = 0
STATE_PLAYING = 1
STATE_PAUSED = 2
STATE_DEAD = 3
STATE_LEVEL_CLEAR = 4
STATE_GAME_OVER = 5

# Directions
DIR_VECTORS = {
    'UP': (0, -1),
    'DOWN': (0, 1),
    'LEFT': (-1, 0),
    'RIGHT': (1, 0),
}

HOLE_TRAP_TIME = 3.5  # seconds guard is trapped
HOLE_FILL_TIME = 5.0  # seconds before hole auto-fills

# ANSI 256-color palette
C = {
    'wall_fg': 94,
    'wall_bg': 17,
    'wall_hi': 130,
    'dirt_fg': 137,
    'dirt_bg': 58,
    'dirt_spk': 100,
    'dug_bg': 233,
    'gold_fg': 220,
    'gold_shim': 228,
    'gold_bg': 58,
    'hole_fg': 0,
    'hole_bg': 236,
    'hole_edg': 239,
    'player_fg': 255,
    'guard_fg': 196,
    'guard_dim': 124,
    'guard_chase': 201,
    'border_fg': 240,
    'text': 255,
    'text_dim': 244,
    'score': 220,
    'title': 220,
    'life': 196,
    'flash': 255,
}


# ============================================================================
# Terminal helpers
# ============================================================================

class TerminalManager:
    """Manage terminal raw mode and cleanup."""

    def __init__(self):
        self.fd = sys.stdin.fileno()
        self._old = None
        self._raw = False

    def enter_raw(self):
        if not self._raw:
            self._old = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
            self._raw = True

    def exit_raw(self):
        if self._raw and self._old is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self._old)
            self._raw = False

    def write(self, s):
        sys.stdout.write(s)

    def flush(self):
        sys.stdout.flush()

    def cleanup(self):
        self.exit_raw()
        self.write('\033[?25h\033[0m\033[2J\033[H')
        self.flush()


TERM = TerminalManager()


def ansi(fg=None, bg=None, bold=False):
    parts = []
    if bold:
        parts.append('1')
    if fg is not None:
        parts.append(f'38;5;{fg}')
    if bg is not None:
        parts.append(f'48;5;{bg}')
    if not parts:
        return '\033[0m'
    return '\033[' + ';'.join(parts) + 'm'


def cls():
    return '\033[2J\033[H'


def hide_cursor():
    return '\033[?25l'


def move_to(row, col):
    """ANSI cursor positioning (1-based)."""
    return f'\033[{row};{col}H'


def term_size():
    cols, rows = shutil.get_terminal_size()
    return cols, rows


# ============================================================================
# Cell rendering helpers
# ============================================================================

def cell_shade(x, y, fg_a, bg_a, char_a):
    """Pick a variant based on position hash for stable texturing."""
    h = (x * 7 + y * 31) & 0x3F
    if h < 8:
        return fg_a[0], bg_a[0], char_a[0]
    elif h < 20:
        return fg_a[1], bg_a[1], char_a[1]
    elif h < 40:
        return fg_a[2], bg_a[2], char_a[2]
    else:
        return fg_a[3], bg_a[3], char_a[3]


def render_wall(x, y):
    fg_vars = [C['wall_bg'], C['wall_fg'], C['wall_fg'], C['wall_hi']]
    bg_vars = [C['wall_bg'], C['wall_bg'], C['wall_bg'], C['wall_bg']]
    ch_vars = ['██', '██', '▓▓', '▓▓']
    return cell_shade(x, y, fg_vars, bg_vars, ch_vars)


def render_dirt(x, y):
    fg_vars = [C['dirt_fg'], C['dirt_fg'], C['dirt_spk'], C['dirt_bg']]
    bg_vars = [C['dirt_bg'], C['dirt_bg'], C['dirt_bg'], C['dirt_bg']]
    ch_vars = ['▒▒', '▓▓', '░░', '░░']
    return cell_shade(x, y, fg_vars, bg_vars, ch_vars)


# ============================================================================
# Level data
# Legend: #=wall  .=dug  (space)=dirt  $=gold  @=player  &=guard
# ============================================================================

LEVEL_DATA = [
    {
        'name': 'Training Grounds',
        'data': [
            '################################',
            '#..............................#',
            '#..     ....      ....     ..#',
            '#..  $  ....  $   ....  $  ..#',
            '#..     ....      ....     ..#',
            '#..............................#',
            '#....   ....   ....   .......#',
            '#.... & ....   .... & .......#',
            '#....   ....   ....   .......#',
            '#..............................#',
            '#..     ....  $   ....     ..#',
            '#..  $  ....      ....  $  ..#',
            '#..     ....      ....     ..#',
            '#..............................#',
            '################################',
        ],
    },
    {
        'name': 'Maze of Earth',
        'data': [
            '################################',
            '#..............................#',
            '#..  WWWWW..     ..WWWWW.....#',
            '#..  W........$........W.....#',
            '#..  WWWWW..     ..WWWWW.....#',
            '#..............................#',
            '#.......WWWWWWWWWWWWW........#',
            '#..$....W.............W...&...#',
            '#.......W.....@.......W......#',
            '#.......WWWWWWWWWWWWW........#',
            '#..............................#',
            '#..  WWWWW..     ..WWWWW.....#',
            '#.. &W........$........W.....#',
            '#..  WWWWW..     ..WWWWW.....#',
            '################################',
        ],
    },
    {
        'name': 'Underground Fortress',
        'data': [
            '################################',
            '#..  WWWWWWWWWWWWWWWWWWWW  ..#',
            '#..  W..................W  ..#',
            '#..  W..  $   WW   $  ..W  ..#',
            '#..  W..     WW     ..  W  ..#',
            '#..  W..  &  WW  &  ..  W  ..#',
            '#..  W..................W  ..#',
            '#..  WWWWW  ....  WWWWW  ..#',
            '#........  ....  ....  .....#',
            '#..  WWWWW  ....  WWWWW  ..#',
            '#..  W..................W  ..#',
            '#..  W..     $  $    ..W  ..#',
            '#..  W..................W  ..#',
            '#..  WWWWWWWWWWWWWWWWWWWW  ..#',
            '################################',
        ],
    },
    {
        'name': 'Cavern of Riches',
        'data': [
            '################################',
            '#..............................#',
            '#....WWW..........WWW.........#',
            '#....W.W..$....$..W.W.........#',
            '#....WWW..........WWW.........#',
            '#.......WWW....WWW............#',
            '#..$....W.W....W.W.....$......#',
            '#.......WWW....WWW............#',
            '#....WWW..........WWW.........#',
            '#....W.W..&..&..W.W.........#',
            '#....WWW..........WWW.........#',
            '#..............................#',
            '#..WWWWWW..........WWWWWW.....#',
            '#..............................#',
            '################################',
        ],
    },
    {
        'name': 'Mirror Mine',
        'data': [
            '################################',
            '#..............................#',
            '#..  WWWWW........WWWWW  .....#',
            '#..  W...W...$...W...W  .....#',
            '#..  WWWWW........WWWWW  .....#',
            '#..............................#',
            '#...WWWWWWW....WWWWWWW........#',
            '#...W.......&..&.......W......#',
            '#...WWWWWWW....WWWWWWW........#',
            '#..............................#',
            '#..  WWWWW........WWWWW  .....#',
            '#..  W...W...$...W...W  .....#',
            '#..  WWWWW........WWWWW  .....#',
            '#..............................#',
            '################################',
        ],
    },
    {
        'name': 'The Warren',
        'data': [
            '################################',
            '#..WWWWWW..WWWW..WWWWWW.....#',
            '#..W......W....W......W.....#',
            '#..W..$...W....W...$..W.....#',
            '#..W......W....W......W.....#',
            '#..WWWWWW..WWWW..WWWWWW.....#',
            '#..............................#',
            '#..WWWW..WWWWWWWW..WWWW.....#',
            '#..W....W........W....W.....#',
            '#..W &..W..$..$..W..& W.....#',
            '#..W....W........W....W.....#',
            '#..WWWW..WWWWWWWW..WWWW.....#',
            '#..............................#',
            '#..................................#',
            '################################',
        ],
    },
    {
        'name': 'Deep Earth',
        'data': [
            '################################',
            '#..............................#',
            '#..WWWW....WWWWWW....WWWW...#',
            '#..W..W....W....W....W..W...#',
            '#..W..W..$ W....W $..W..W...#',
            '#..WWWW....WWWWWW....WWWW...#',
            '#..............................#',
            '#..WWWWWWWWWW..WWWWWWWWWW...#',
            '#..W..................W.....#',
            '#..W..&..&..&..&..&..W.....#',
            '#..W..................W.....#',
            '#..WWWWWWWWWW..WWWWWWWWWW...#',
            '#..............................#',
            '#..................................#',
            '################################',
        ],
    },
    {
        'name': 'The Gauntlet',
        'data': [
            '################################',
            '#..WWWWWWWWWWWWWWWWWWWWWW..#',
            '#..W....WW....WW....WW....W..#',
            '#..W $..WW..$ WW..$ ..WW..W..#',
            '#..W....WW....WW....WW....W..#',
            '#..WWWW....WWWWWW....WWWW..#',
            '#.......W..........W.........#',
            '#.......W..&....&..W.........#',
            '#.......W..........W.........#',
            '#..WWWW....WWWWWW....WWWW..#',
            '#..W....WW....WW....WW....W..#',
            '#..W..$ WW..$ WW..$ WW..$ W..#',
            '#..W....WW....WW....WW....W..#',
            '#..WWWWWWWWWWWWWWWWWWWWWW..#',
            '################################',
        ],
    },
    {
        'name': 'Endless Pitt',
        'data': [
            '################################',
            '#..............................#',
            '#..WWWWWW..WWWWWW..WWWWWW...#',
            '#..W........W........W.......#',
            '#..W..$..&..W..&..$..W.......#',
            '#..W........W........W.......#',
            '#..WWWWWW..WWWWWW..WWWWWW...#',
            '#..............................#',
            '#..WWWWWW..WWWWWW..WWWWWW...#',
            '#..W........W........W.......#',
            '#..W..$..&..W..&..$..W.......#',
            '#..W........W........W.......#',
            '#..WWWWWW..WWWWWW..WWWWWW...#',
            '#..............................#',
            '################################',
        ],
    },
    {
        'name': 'Heart of the Mountain',
        'data': [
            '################################',
            '#..WWWWWWWWWWWWWWWWWWWWWW..#',
            '#..W......................W..#',
            '#..W..WWWWWWWWWWWWWWWW..W..#',
            '#..W..W............W..W..#',
            '#..W..W..$$$$$$$$..W..W..#',
            '#..W..W............W..W..#',
            '#..W..WWWWWWWWWWWWWWWW..W..#',
            '#..W......................W..#',
            '#..W..&..&..&..&..&..&..W..#',
            '#..W......................W..#',
            '#..W..WWWWWWWWWWWWWWWW..W..#',
            '#..W......................W..#',
            '#..WWWWWWWWWWWWWWWWWWWWWW..#',
            '################################',
        ],
    },
]


def parse_level(ld):
    """Parse a string level into (grid, player_start, guard_starts, gold_count)."""
    data = ld['data']
    h = len(data)
    w = len(data[0])
    grid = [[CELL_DIRT for _ in range(w)] for _ in range(h)]
    px, py = 1, 1
    guards = []
    gold = 0

    for y in range(h):
        row = data[y]
        for x in range(w):
            ch = row[x] if x < len(row) else ' '
            if ch == '#':
                grid[y][x] = CELL_WALL
            elif ch == '.':
                grid[y][x] = CELL_DUG
            elif ch == ' ':
                grid[y][x] = CELL_DIRT
            elif ch == '$':
                grid[y][x] = CELL_GOLD
                gold += 1
            elif ch == '@':
                grid[y][x] = CELL_DUG
                px, py = x, y
            elif ch == 'W':
                grid[y][x] = CELL_WALL
            elif ch == '&':
                grid[y][x] = CELL_DUG
                guards.append((x, y))
    return grid, px, py, guards, gold, w, h
parse_level(LEVEL_DATA[0])


# ============================================================================
# Particle system
# ============================================================================

class Particle:
    __slots__ = ('x', 'y', 'dx', 'dy', 'life', 'max_life', 'fg', 'char', 'gravity')

    def __init__(self, x, y, dx, dy, life, fg, char='♦', gravity=True):
        self.x = x
        self.y = y
        self.dx = dx
        self.dy = dy
        self.life = life
        self.max_life = life
        self.fg = fg
        self.char = char
        self.gravity = gravity

    def update(self, dt):
        self.x += self.dx * dt
        self.y += self.dy * dt
        if self.gravity:
            self.dy += 6.0 * dt
        self.life -= dt
        return self.life > 0


class Particles:
    def __init__(self):
        self.list = []

    def emit(self, x, y, count=8, fg=220, char='♦', spread=2.0, life=0.6):
        for _ in range(count):
            a = random.uniform(0, 6.283)
            sp = random.uniform(1.0, spread * 3.0)
            self.list.append(Particle(
                x + random.uniform(-0.3, 0.3),
                y + random.uniform(-0.3, 0.3),
                math.cos(a) * sp, math.sin(a) * sp - 1.0,
                random.uniform(life * 0.5, life), fg, char, True
            ))

    def burst(self, x, y, fg=255, count=15):
        for _ in range(count):
            self.list.append(Particle(
                x, y, random.uniform(-6, 6), random.uniform(-6, 6),
                random.uniform(0.3, 0.7), fg, '*', False
            ))

    def update(self, dt):
        self.list = [p for p in self.list if p.update(dt)]

    def render_overlay(self, gw, gh, ox_screen, oy_screen):
        """Return list of (row_1based, col_1based, ansi_string)."""
        out = []
        for p in self.list:
            gx = int(round(p.x))
            gy = int(round(p.y))
            if gx < 0 or gx >= gw or gy < 0 or gy >= gh:
                continue
            alpha = p.life / p.max_life
            if alpha < 0.2:
                continue
            row = oy_screen + gy
            col = ox_screen + gx * 2
            out.append((row, col, ansi(p.fg) + p.char * 2 + ansi(0)))
        return out


# ============================================================================
# Guard
# ============================================================================

class Guard:
    __slots__ = ('x', 'y', 'state', 'trapped_timer', 'move_cd', 'anim_t')

    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.state = GUARD_PATROL
        self.trapped_timer = 0.0
        self.move_cd = random.uniform(0, 0.3)
        self.anim_t = random.uniform(0, 6.28)

    def _neighbors(self, grid, gw, gh):
        """Return list of (nx, ny, dx, dy) reachable from current position."""
        out = []
        for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
            nx, ny = self.x + dx, self.y + dy
            if nx < 1 or nx >= gw - 1 or ny < 1 or ny >= gh - 1:
                continue
            if grid[ny][nx] in (CELL_WALL, CELL_DIRT, CELL_GOLD, CELL_HOLE):
                continue
            out.append((nx, ny, dx, dy))
        return out

    def update(self, dt, grid, px, py, gw, gh):
        self.anim_t += dt * 3.0
        if self.state == GUARD_TRAPPED:
            self.trapped_timer -= dt
            if self.trapped_timer <= 0:
                self.state = GUARD_PATROL
                self.move_cd = 0.5
            return

        self.move_cd -= dt
        if self.move_cd > 0:
            return
        self.move_cd = random.uniform(0.15, 0.3)

        dist = abs(self.x - px) + abs(self.y - py)
        neighbors = self._neighbors(grid, gw, gh)
        if not neighbors:
            return

        if dist == 1:
            # Player is adjacent — flee (Lode Runner style: run away when on top)
            self.state = GUARD_PATROL
            # Move in direction that maximizes distance from player
            best = max(neighbors, key=lambda t: abs(t[0] - px) + abs(t[1] - py))
            self.x, self.y = best[0], best[1]
        elif dist <= 7:
            self.state = GUARD_CHASE
            # Move toward player (closest first)
            best = min(neighbors, key=lambda t: abs(t[0] - px) + abs(t[1] - py))
            self.x, self.y = best[0], best[1]
        else:
            self.state = GUARD_PATROL
            # Random wander
            nb = random.choice(neighbors)
            self.x, self.y = nb[0], nb[1]


# ============================================================================
# Game
# ============================================================================

class Game:
    def __init__(self):
        self.state = STATE_TITLE
        self.running = True

        tw, th = term_size()
        self.term_w = tw
        self.term_h = th

        self.levels = []
        self._prepare_levels()

        self.level_idx = 0
        self.score = 0
        self.high_score = self._read_hi()
        self.lives = 3
        self.extra_life_given = False

        self.grid = []
        self.gw = 0
        self.gh = 0

        self.px = 1
        self.py = 1
        self.guards = []
        self.holes = {}  # (x,y) -> timer

        self.gold_collected = 0
        self.total_gold = 0
        self.particles = Particles()

        self.screen_flash = 0.0
        self.death_timer = 0.0
        self.anim_t = 0.0
        self.title_t = 0.0
        self.level_clear_t = 0.0

        # Status bar centering cache
        self._ox = 0

    def _read_hi(self):
        try:
            with open(os.path.expanduser('~/.goldminer_hi')) as f:
                return int(f.read().strip())
        except Exception:
            return 0

    def _save_hi(self):
        try:
            with open(os.path.expanduser('~/.goldminer_hi'), 'w') as f:
                f.write(str(self.high_score))
        except Exception:
            pass

    def _prepare_levels(self):
        for ld in LEVEL_DATA:
            grid, px, py, guards, gold, w, h = parse_level(ld)
            self.levels.append({
                'name': ld['name'],
                'grid': grid,
                'px': px,
                'py': py,
                'guards': guards,
                'gold': gold,
                'w': w,
                'h': h,
            })

    def start_level(self, idx):
        if idx >= len(self.levels):
            self.state = STATE_TITLE
            return
        lv = self.levels[idx]
        self.grid = [row[:] for row in lv['grid']]
        self.gw = lv['w']
        self.gh = lv['h']
        self.px = lv['px']
        self.py = lv['py']
        self.guards = [Guard(gx, gy) for gx, gy in lv['guards']]
        self.holes = {}
        self.gold_collected = 0
        self.total_gold = lv['gold']
        self.particles = Particles()
        self.screen_flash = 0.0
        self.death_timer = 0.0
        self.level_clear_t = 0.0
        self.state = STATE_PLAYING

    def cell(self, x, y):
        if x < 0 or x >= self.gw or y < 0 or y >= self.gh:
            return CELL_WALL
        return self.grid[y][x]

    def dig_hole(self, dx, dy):
        """Dig a hole trap in direction (dx,dy)."""
        if self.state != STATE_PLAYING:
            return
        nx, ny = self.px + dx, self.py + dy
        if nx < 1 or nx >= self.gw - 1 or ny < 1 or ny >= self.gh - 1:
            return
        if self.grid[ny][nx] != CELL_DIRT:
            return
        self.grid[ny][nx] = CELL_HOLE
        self.holes[(nx, ny)] = HOLE_FILL_TIME
        self._bell()
        self.particles.emit(nx, ny, count=6, fg=C['hole_edg'], char='▓', spread=1.5, life=0.4)
        # Trap any guard standing there
        for g in self.guards:
            if g.x == nx and g.y == ny and g.state != GUARD_TRAPPED:
                g.state = GUARD_TRAPPED
                g.trapped_timer = HOLE_TRAP_TIME
                self.particles.emit(nx, ny, count=8, fg=C['guard_dim'], char='☻', spread=2.0, life=0.6)

    def _bell(self):
        sys.stdout.write('\a')
        sys.stdout.flush()

    # --- Input ---

    def handle_key(self, key):
        if self.state == STATE_TITLE:
            if key in ('q', 'Q'):
                self.running = False
            elif key in ('\r', '\n', ' '):
                self.level_idx = 0
                self.score = 0
                self.lives = 3
                self.extra_life_given = False
                self.start_level(0)
            return

        if self.state == STATE_GAME_OVER:
            if key in (' ', '\r', '\n'):
                self.level_idx = 0
                self.score = 0
                self.lives = 3
                self.extra_life_given = False
                self.start_level(0)
            elif key in ('q', 'Q'):
                self.state = STATE_TITLE
            return

        if self.state == STATE_DEAD:
            if self.death_timer > 0:
                return
            if key in (' ', '\r', '\n'):
                self._respawn()
            return

        if self.state == STATE_LEVEL_CLEAR:
            if key in (' ', '\r', '\n'):
                self.level_idx += 1
                self.start_level(self.level_idx)
            return

        if self.state == STATE_PAUSED:
            if key in ('p', 'P'):
                self.state = STATE_PLAYING
            return

        # STATE_PLAYING
        if key in ('p', 'P'):
            self.state = STATE_PAUSED
            return
        if key in ('q', 'Q'):
            self.running = False
            return
        if key == ' ':
            self._tick_guards()
            return

        if self.death_timer > 0:
            return

        dx, dy = 0, 0
        if key in ('UP', 'w', 'W'):
            dy = -1
        elif key in ('DOWN', 's', 'S'):
            dy = 1
        elif key in ('LEFT', 'a', 'A'):
            dx = -1
        elif key in ('RIGHT', 'd', 'D'):
            dx = 1
        elif key in ('z', 'Z'):
            self.dig_hole(-1, 0)
            return
        elif key in ('x', 'X'):
            self.dig_hole(1, 0)
            return
        else:
            return

        if dx == 0 and dy == 0:
            return

        nx, ny = self.px + dx, self.py + dy
        if nx < 1 or nx >= self.gw - 1 or ny < 1 or ny >= self.gh - 1:
            return

        ct = self.cell(nx, ny)
        if ct == CELL_WALL:
            return
        if ct == CELL_DIRT:
            self.grid[ny][nx] = CELL_DUG
        elif ct == CELL_GOLD:
            self.grid[ny][nx] = CELL_DUG
            self.gold_collected += 1
            self.score += 100
            self._bell()
            self.particles.emit(nx, ny, count=12, fg=C['gold_fg'], char='♦', spread=2.5, life=0.8)
            if self.gold_collected >= self.total_gold:
                self._level_clear()
                return
            if self.score >= 5000 and not self.extra_life_given:
                self.lives += 1
                self.extra_life_given = True
        elif ct == CELL_HOLE:
            pass  # Walk over

        self._tick_guards()
        self.px, self.py = nx, ny
        self._check_guard_collision()

    def _tick_guards(self):
        for g in self.guards:
            if g.state != GUARD_TRAPPED:
                g.move_cd = 0
                g.update(DT * 2, self.grid, self.px, self.py, self.gw, self.gh)

    def _check_guard_collision(self):
        for g in self.guards:
            if g.state == GUARD_TRAPPED:
                continue
            if g.x == self.px and g.y == self.py:
                self._player_die()
                return

    def _level_clear(self):
        self.state = STATE_LEVEL_CLEAR
        self.score += self.lives * 50
        self.particles.burst(self.gw // 2, self.gh // 2, C['gold_fg'], 25)
        if self.score > self.high_score:
            self.high_score = self.score
            self._save_hi()

    def _respawn(self):
        lv = self.levels[self.level_idx]
        self.px = lv['px']
        self.py = lv['py']
        self.death_timer = 0.0
        self.screen_flash = 0.0
        self.state = STATE_PLAYING

    def _player_die(self):
        self.lives -= 1
        self.screen_flash = 0.4
        self.death_timer = 1.2
        self.particles.burst(self.px, self.py, C['flash'], 25)
        self._bell()
        if self.lives <= 0:
            self.state = STATE_GAME_OVER
            if self.score > self.high_score:
                self.high_score = self.score
                self._save_hi()
        else:
            self.state = STATE_DEAD

    # --- Update ---

    def update(self, dt):
        self.anim_t += dt
        self.title_t += dt
        self.screen_flash = max(0, self.screen_flash - dt)
        self.death_timer = max(0, self.death_timer - dt)

        for g in self.guards:
            g.update(dt, self.grid, self.px, self.py, self.gw, self.gh)

        # Guard-hole interaction
        for g in self.guards:
            if g.state == GUARD_TRAPPED:
                g.trapped_timer -= dt
                if g.trapped_timer <= 0:
                    g.state = GUARD_PATROL
                    g.move_cd = 0.5
                    if (g.x, g.y) in self.holes:
                        del self.holes[(g.x, g.y)]
                    if 0 <= g.y < self.gh and 0 <= g.x < self.gw:
                        self.grid[g.y][g.x] = CELL_DUG
                continue
            if (g.x, g.y) in self.holes and g.state != GUARD_TRAPPED:
                g.state = GUARD_TRAPPED
                g.trapped_timer = HOLE_TRAP_TIME
                self.particles.emit(g.x, g.y, count=6, fg=C['guard_dim'], char='▓', spread=1.5, life=0.5)

        # Hole auto-fill timers
        expired = []
        for (hx, hy), timer in self.holes.items():
            if self.cell(hx, hy) != CELL_HOLE:
                expired.append((hx, hy))
                continue
            t = timer - dt
            if t <= 0:
                self.grid[hy][hx] = CELL_DUG
                expired.append((hx, hy))
            else:
                self.holes[(hx, hy)] = t
        for k in expired:
            self.holes.pop(k, None)

        if self.state == STATE_PLAYING and self.death_timer <= 0:
            self._check_guard_collision()

        self.particles.update(dt)

        # Terminal resize check
        tw, th = term_size()
        if tw != self.term_w or th != self.term_h:
            self.term_w = tw
            self.term_h = th

    # --- Render ---

    def render(self):
        buf = [hide_cursor(), cls()]

        tw, th = self.term_w, self.term_h
        oy = 3  # first row of grid (1-based)
        ox = max(2, (tw - self.gw * 2) // 2)

        # Status line
        stat = (
            f"  {ansi(C['score'], bold=True)}SCORE{ansi()} {ansi(255)}{self.score:06d}{ansi()}"
            f"  {ansi(C['text_dim'])}HI{ansi()} {ansi(C['score'])}{self.high_score:06d}{ansi()}"
            f"  {ansi(C['text_dim'])}LEVEL{ansi()} {ansi(255)}{self.level_idx + 1}{ansi()}"
            f"  {ansi(C['gold_fg'])}GOLD{ansi()} {ansi(255)}{self.gold_collected}/{self.total_gold}{ansi()}"
            f"  {ansi(C['life'])}LIVES{ansi()} {ansi(255)}{'♥' * self.lives}{'♡' * max(0, 3 - self.lives)}{ansi()}"
        )
        if self.state in (STATE_PLAYING, STATE_PAUSED, STATE_DEAD):
            lv = self.levels[self.level_idx]
            stat += f"  {ansi(C['text_dim'])}{lv['name']}{ansi()}"
        buf.append(stat)
        buf.append('\n')

        # Top border
        pad = ' ' * ox
        buf.append(pad + ansi(C['border_fg']) + '╔' + '═' * (self.gw * 2) + '╗' + ansi())
        buf.append('\n')

        shimmer = int(self.anim_t * 4) % 2 == 0

        for y in range(self.gh):
            buf.append(pad + ansi(C['border_fg']) + '║' + ansi())
            for x in range(self.gw):
                ct = self.cell(x, y)

                # Check entity presence
                is_player = (x == self.px and y == self.py)
                is_guard = None
                guard_trapped = False
                for g in self.guards:
                    if int(round(g.x)) == x and int(round(g.y)) == y:
                        is_guard = g
                        guard_trapped = (g.state == GUARD_TRAPPED)
                        break

                if is_player:
                    pulse = 0.5 + 0.5 * math.sin(self.anim_t * 6)
                    b = int(200 + 55 * pulse)
                    buf.append(ansi(b, bold=True) + '@@')
                elif is_guard and guard_trapped:
                    buf.append(ansi(C['guard_dim']) + '☻☻')
                elif is_guard:
                    if is_guard.state == GUARD_CHASE:
                        pulse = 0.6 + 0.4 * math.sin(self.anim_t * 8 + is_guard.anim_t)
                        fg = int(C['guard_chase'] * (0.7 + 0.3 * pulse))
                        buf.append(ansi(fg, bold=True) + '☻☻')
                    else:
                        pulse = 0.6 + 0.4 * math.sin(self.anim_t * 5 + is_guard.anim_t)
                        fg = int(C['guard_fg'] * (0.6 + 0.4 * pulse))
                        buf.append(ansi(fg) + '☻☻')
                else:
                    if ct == CELL_WALL:
                        fg, bg, ch = render_wall(x, y)
                    elif ct == CELL_DIRT:
                        fg, bg, ch = render_dirt(x, y)
                    elif ct == CELL_DUG:
                        fg, bg, ch = C['text_dim'], C['dug_bg'], '  '
                    elif ct == CELL_GOLD:
                        fg = C['gold_shim'] if shimmer else C['gold_fg']
                        bg = C['gold_bg']
                        ch = '◆◆'
                    elif ct == CELL_HOLE:
                        h = (x * 13 + y * 7) & 0x3F
                        bg = C['hole_edg'] if h < 10 else C['hole_bg']
                        fg = C['hole_fg']
                        ch = '▓▓'
                    else:
                        fg, bg, ch = C['text'], 0, '??'
                    buf.append(ansi(fg, bg) + ch)
                buf.append(ansi())
            buf.append(ansi(C['border_fg']) + '║' + ansi())
            buf.append('\n')

        # Bottom border
        buf.append(pad + ansi(C['border_fg']) + '╚' + '═' * (self.gw * 2) + '╝' + ansi())
        buf.append('\n')

        # Screen flash
        if self.screen_flash > 0:
            intensity = min(255, int(255 * self.screen_flash / 0.4))
            bk = min(255, int(200 * self.screen_flash / 0.4))
            for r in range(1, th + 1):
                buf.append(move_to(r, 1) + ansi(bk, bg=intensity) + ' ' * tw + ansi())

        # Overlays
        if self.state == STATE_TITLE:
            self._title_overlay(buf, tw, th)
        elif self.state == STATE_PAUSED:
            self._center_box(buf, tw, th, '  PAUSED  ', C['gold_fg'])
        elif self.state == STATE_DEAD and self.death_timer <= 0:
            self._center_box(buf, tw, th, '  LOST A LIFE!  ', C['life'])
            buf.append(move_to(th // 2 + 2, (tw - 28) // 2))
            buf.append(ansi(C['text_dim']) + 'Press SPACE to continue' + ansi())
        elif self.state == STATE_LEVEL_CLEAR:
            self._center_box(buf, tw, th, '  LEVEL CLEAR!  ', C['gold_fg'])
            buf.append(move_to(th // 2 + 2, (tw - 22) // 2))
            buf.append(ansi(C['text_dim']) + f'Score: {ansi(C["score"])}{self.score}' + ansi())
            if int(self.anim_t * 2) % 2 == 0:
                buf.append(move_to(th // 2 + 3, (tw - 28) // 2))
                buf.append(ansi(C['text_dim']) + 'Press SPACE to continue' + ansi())
        elif self.state == STATE_GAME_OVER:
            self._center_box(buf, tw, th, '  GAME OVER!  ', C['life'])
            buf.append(move_to(th // 2 + 2, (tw - 20) // 2))
            buf.append(ansi(C['text_dim']) + f'Score: {ansi(C["score"])}{self.score}' + ansi())
            if self.score == self.high_score and self.score > 0:
                buf.append(move_to(th // 2 + 3, (tw - 24) // 2))
                buf.append(ansi(C['gold_fg'], bold=True) + '  NEW HIGH SCORE!  ' + ansi())
            if int(self.anim_t * 2) % 2 == 0:
                buf.append(move_to(th // 2 + 5, (tw - 26) // 2))
                buf.append(ansi(C['text_dim']) + 'Press SPACE to retry' + ansi())

        # Particles overlay (grid-relative to screen)
        ox_screen = ox + 3  # pad + '║' = (ox spaces) + ║ ; but pad is already ox, then we add ║
        # Actually: pad(ox) + ║ = ox+1 characters before the grid cells (position-wise)
        # In 1-based coords: col = ox + 2 (one for pad + 1 for the char position... wait)
        pad_chars = ox + 1  # ox spaces + ║ = ox+1 chars offset
        # Grid cell[0] starts at column pad_chars + 1 = ox + 2 (1-based)
        # But in 1-based coordinates, after ox spaces (starting at col 1):
        # col = ox + 1 is the ║ character
        # col = ox + 2 is the first grid cell (first char of 2-char cell)
        # So cell[gx] starts at column: ox + 2 + gx * 2
        cell_offset_col = ox + 2
        cell_offset_row = oy  # grid starts at row 3
        for row, col, ps in self.particles.render_overlay(self.gw, self.gh, cell_offset_col, cell_offset_row):
            if 1 <= row <= th and 1 <= col <= tw - 1:
                buf.append(move_to(row, col) + ps)

        # Cursor to bottom
        buf.append(move_to(th, 1))

        TERM.write(''.join(buf))
        TERM.flush()

    def _title_overlay(self, buf, tw, th):
        title = [
            "  ▄████  ██▓    ██████  ██████ ",
            " ██▒ ▀█▒▓██▒   ▒██    ▒ ▒██    ▒ ",
            "▒██░▄▄▄░▒██▒   ░ ▓██▄   ░ ▓██▄  ",
            "░▓█  ██▓░██░     ▒   ██▒  ▒   ██▒",
            "░▒▓███▀▒░██░   ▒██████▒▒▒██████▒▒",
            " ░▒   ▒ ░▓     ▒ ▒▓▒ ▒ ░▒ ▒▓▒ ▒ ░",
            "  ░   ░  ▒ ░   ░ ░▒  ░ ░░ ░▒  ░ ░",
            "  ░ ░ ░  ▒ ░ ░  ░  ░  ░  ░  ░  ░",
            "      ░  ░           ░        ░ ",
        ]
        ty = max(1, th // 2 - 9)
        tx = max(0, (tw - 34) // 2)

        for i, line in enumerate(title):
            buf.append(move_to(ty + i, tx + 1))
            buf.append(ansi(C['title'], bold=True))
            # Occasional shimmer
            for ch in line:
                if ch == '█' and random.random() < 0.08:
                    buf.append(ansi(C['gold_shim'], bold=True) + ch + ansi(C['title'], bold=True))
                else:
                    buf.append(ch)
            buf.append(ansi())

        sub = ty + len(title) + 1
        buf.append(move_to(sub, max(1, (tw - 34) // 2)))
        buf.append(ansi(C['text_dim']) + '  A Tunnel-Digging Gold Rush Adventure' + ansi())

        instr = [
            '[ WASD / Arrows ]  Move & Dig',
            '[ Z ]  Dig hole to the LEFT',
            '[ X ]  Dig hole to the RIGHT',
            '[ SPACE ]  Wait a turn',
            '[ P ]  Pause  |  [ Q ]  Quit',
        ]
        iy = sub + 2
        for i, text in enumerate(instr):
            buf.append(move_to(iy + i, max(1, (tw - 38) // 2)))
            buf.append(ansi(C['text']) + text + ansi())

        pyy = iy + len(instr) + 2
        if int(self.title_t * 2) % 2 == 0:
            buf.append(move_to(pyy, max(1, (tw - 22) // 2)))
            buf.append(ansi(C['gold_fg'], bold=True) + '  PRESS ENTER TO START' + ansi())

        if self.high_score > 0:
            buf.append(move_to(pyy + 3, max(1, (tw - 24) // 2)))
            buf.append(ansi(C['text_dim']) + f'High Score: {ansi(C["score"])}{self.high_score:06d}' + ansi())

    def _center_box(self, buf, tw, th, text, fg):
        y = th // 2 - 1
        x = (tw - len(text.replace('\033', '')) - 4) // 2
        if x < 0:
            x = 0
        # Use a fixed width
        bw = max(len(text) + 4, 20)
        x = (tw - bw) // 2
        buf.append(move_to(y, x) + ansi(0, bg=236) + ' ' * bw + ansi())
        buf.append(move_to(y + 1, x) + ansi(0, bg=236) + ' ' + ansi(fg, bg=236, bold=True) + text + ansi(0, bg=236) + ' ' + ansi())
        buf.append(move_to(y + 2, x) + ansi(0, bg=236) + ' ' * bw + ansi())

    # --- Input polling ---

    def poll(self):
        try:
            r, _, _ = select.select([sys.stdin], [], [], 0)
            if not r:
                return
            data = os.read(sys.stdin.fileno(), 1024)
            if not data:
                return
            i = 0
            while i < len(data):
                b = data[i]
                if b == 0x1b:
                    if i + 2 < len(data) and data[i + 1] == 0x5b:
                        seq = data[i:i+3]
                        if seq == b'\x1b[A':
                            self.handle_key('UP')
                            i += 3; continue
                        elif seq == b'\x1b[B':
                            self.handle_key('DOWN')
                            i += 3; continue
                        elif seq == b'\x1b[C':
                            self.handle_key('RIGHT')
                            i += 3; continue
                        elif seq == b'\x1b[D':
                            self.handle_key('LEFT')
                            i += 3; continue
                    i += 1; continue
                elif b in (0x03, 0x1a):
                    self.running = False
                    return
                key_map = {
                    0x0a: '\n', 0x0d: '\n', 0x20: ' ',
                    0x70: 'p', 0x50: 'P',
                    0x71: 'q', 0x51: 'Q',
                    0x77: 'w', 0x57: 'W',
                    0x73: 's', 0x53: 'S',
                    0x61: 'a', 0x41: 'A',
                    0x64: 'd', 0x44: 'D',
                    0x7a: 'z', 0x5a: 'Z',
                    0x78: 'x', 0x58: 'X',
                }
                if b in key_map:
                    self.handle_key(key_map[b])
                i += 1
        except (select.error, OSError):
            pass

    # --- Main loop ---

    def run(self):
        signal.signal(signal.SIGWINCH, lambda *_: None)
        try:
            TERM.enter_raw()
            self.running = True
            last = time.time()
            acc = 0.0

            while self.running:
                now = time.time()
                ft = now - last
                last = now
                if ft > 0.1:
                    ft = DT
                acc += ft

                self.poll()

                while acc >= DT:
                    self.update(DT)
                    acc -= DT

                self.render()
                time.sleep(0.001)
        finally:
            TERM.cleanup()


# ============================================================================
# Main
# ============================================================================

def main():
    try:
        g = Game()
        g.run()
    except Exception as e:
        TERM.cleanup()
        print(f'\nError: {e}')
        import traceback
        traceback.print_exc()
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
