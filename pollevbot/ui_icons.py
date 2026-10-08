"""Small Tk icons drawn in code, with no font or image dependencies."""

import math
import tkinter as tk


def make_icons(master, color):
    segments = {
        'person': [(3, 15, 4, 12), (4, 12, 7, 10), (7, 10, 11, 10),
                   (11, 10, 14, 12), (14, 12, 15, 15), (3, 15, 15, 15)],
        'document': [(4, 3, 10, 3), (10, 3, 14, 7), (14, 7, 14, 15),
                     (14, 15, 4, 15), (4, 15, 4, 3), (10, 3, 10, 7),
                     (10, 7, 14, 7), (6, 10, 12, 10), (6, 13, 10, 13)],
        'timer': [(9, 5, 9, 9), (9, 9, 12, 11)],
        'logout': [(8, 3, 3, 3), (3, 3, 3, 15), (3, 15, 8, 15),
                   (8, 9, 16, 9), (12, 5, 16, 9), (16, 9, 12, 13)],
        'chevron': [(5, 7, 9, 11), (9, 11, 13, 7)],
        'play': [], 'stop': [],
    }
    circles = {'person': (9, 5, 2.5), 'timer': (9, 9, 6.5)}

    def near_line(x, y, line):
        x1, y1, x2, y2 = line
        dx, dy = x2 - x1, y2 - y1
        t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
        return math.hypot(x - x1 - t * dx, y - y1 - t * dy) <= 0.8

    icons = {}
    for name, lines in segments.items():
        icon = tk.PhotoImage(master=master, width=18, height=18)
        for y in range(18):
            for x in range(18):
                filled = any(near_line(x, y, line) for line in lines)
                if name in circles:
                    cx, cy, radius = circles[name]
                    filled |= abs(math.hypot(x - cx, y - cy) - radius) <= 0.8
                if name == 'play':
                    filled = 3 <= y <= 15 and 5 <= x <= 14 - 1.5 * abs(y - 9)
                elif name == 'stop':
                    filled = 4 <= x <= 13 and 4 <= y <= 13
                if filled:
                    icon.put(color, (x, y))
        icons[name] = icon
    return icons
