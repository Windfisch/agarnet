"""
Copyright (C) 2015  Gjum

code.gjum@gmail.com

This file is part of pyagario.

pyagario is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

pyagario is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with pyagario.  If not, see <http://www.gnu.org/licenses/>.
"""

import random
import sys

from gi.repository import Gtk, GLib, Gdk

from agario.client import Client
from agario.utils import special_names, get_party_address, find_server
from .draw_hud import *
from .draw_cells import *
from .draw_background import *
from .drawutils import *
from .subscriber import MultiSubscriber, Subscriber
from .window import WorldViewer


class NativeControl(Subscriber):
    def __init__(self, client, key_movement_lines=ord('l')):
        self.client = client
        self.movement_delta = Vec()
        self.show_movement_lines = True
        self.key_movement_lines = key_movement_lines

    @property
    def mouse_world(self):
        return self.client.player.center + self.movement_delta

    def send_mouse(self):
        self.client.send_target(*self.mouse_world)

    def on_world_update_post(self):
        self.send_mouse()

    def on_mouse_moved(self, pos, pos_world):
        self.movement_delta = pos_world - self.client.player.center
        self.send_mouse()

    def on_key_pressed(self, val, char):
        if char == 'w':
            self.send_mouse()
            self.client.send_shoot()
        elif val == Gdk.KEY_space:
            self.send_mouse()
            self.client.send_split()
        elif char == 'k':
            self.client.send_explode()
        elif val == self.key_movement_lines:
            self.show_movement_lines = not self.show_movement_lines

    def on_draw_cells(self, c, w):
        if self.show_movement_lines:
            mouse_pos = w.world_to_screen_pos(self.mouse_world)
            c.set_source_rgba(*to_rgba(BLACK, .3))
            for cell in self.client.player.own_cells:
                c.move_to(*w.world_to_screen_pos(cell.pos))
                c.line_to(*mouse_pos)
                c.stroke()


def format_log(lines, width, indent='  '):
    width = int(width)
    for l in lines:
        ind = ''
        while len(l) > len(ind):
            yield l[:width]
            ind = indent
            l = ind + l[width:]


class Logger(Subscriber):
    def __init__(self, client):
        self.client = client
        self.log_msgs = []
        self.leader_best = 11 # outside leaderboard, to show first msg on >=10

    def on_log_msg(self, msg, update=0):
        """
        Updates last `update` msgs with new data.
        Compares first 5 chars or up to first space.
        Set update=0 for no updating.
        """
        first_space = msg.index(' ') if ' ' in msg else 5
        for i, log_msg in enumerate(reversed(
                self.log_msgs[-update:] if update else [])):
            if msg[:first_space] == log_msg[:first_space]:
                self.log_msgs[-i - 1] = msg
                break
        else:
            self.log_msgs.append(msg)
            try:
                print('[LOG]', msg)
            except UnicodeEncodeError:
                pass

    def on_update_msg(self, msg, update=9):
        self.on_log_msg(msg=msg, update=update)

    def on_sock_open(self):
        self.on_update_msg('Connected to %s' % self.client.address)
        self.on_update_msg('Token: %s' % self.client.token)

    def on_world_rect(self, **kwargs):
        self.on_update_msg('World is from %(left)i:%(top)i to %(right)i:%(bottom)i' % kwargs)

    def on_cell_eaten(self, eater_id, eaten_id):
        player = self.client.player
        if eaten_id in player.own_ids:
            name = 'Someone'
            if eater_id in player.world.cells:
                name = '"%s"' % player.world.cells[eater_id].name
            what = 'killed' if len(player.own_ids) <= 1 else 'ate'
            msg = '%s %s me!' % (name, what)
            self.on_update_msg(msg)

    def on_world_update_post(self):
        player = self.client.player
        x, y = player.center
        self.on_update_msg('Mass: %i Pos: (%.2f %.2f)' % (player.total_mass, x, y))

    def on_own_id(self, cid):
        if len(self.client.player.own_ids) == 1:
            self.on_log_msg('Respawned as %s' % self.client.player.nick)
        else:
            self.on_update_msg('Split into %i cells' % len(self.client.player.own_ids))

    def on_leaderboard_names(self, leaderboard):
        if not self.client.player.own_ids:
            return
        our_cid = min(c.cid for c in self.client.player.own_cells)
        for rank, (cid, name) in enumerate(leaderboard):
            if cid == our_cid:
                rank += 1  # start at rank 1
                self.leader_best = min(rank, self.leader_best)
                msg = 'Leaderboard: %i. (best: %i.)' % (rank, self.leader_best)
                self.on_update_msg(msg)

    def on_draw_hud(self, c, w):
        # scrolling log
        log_line_h = 12
        log_char_w = 6  # seems to work with my font

        log = list(format_log(self.log_msgs, w.INFO_SIZE / log_char_w))
        num_log_lines = min(len(log), int(w.INFO_SIZE / log_line_h))

        y_start = w.win_size.y - num_log_lines*log_line_h + 9

        c.set_source_rgba(*to_rgba(BLACK, .3))
        c.rectangle(0, w.win_size.y - num_log_lines*log_line_h,
                    w.INFO_SIZE, num_log_lines*log_line_h)
        c.fill()

        for i, text in enumerate(log[-num_log_lines:]):
            draw_text_left(c, (0, y_start + i*log_line_h),
                           text, size=10, face='monospace')


def gtk_watch_client(client):
    # watch clinet's websocket in GTK main loop
    # `or True` is for always returning True to keep watching
    GLib.io_add_watch(client.ws, GLib.IO_IN, lambda ws, _: client.on_message() or True)
    GLib.io_add_watch(client.ws, GLib.IO_ERR, lambda ws, _: client.subscriber.on_sock_error() or True)
    GLib.io_add_watch(client.ws, GLib.IO_HUP, lambda ws, _: client.disconnect() or True)


def gtk_main_loop():
    # Gtk.main() swallows exceptions, get them back
    sys.excepthook = lambda *args: sys.__excepthook__(*args) or sys.exit()

    Gtk.main()


class GtkControl(Subscriber):
    def __init__(self, address, token=None, nick=None):
        if nick is None: nick = random.choice(special_names)

        # connect the subscribers
        # order is important, first subscriber gets called first

        multi_sub = MultiSubscriber(self)

        self.client = client = Client(multi_sub)

        multi_sub.sub(NativeControl(client))
        multi_sub.sub(Logger(client))

        # background
        multi_sub.sub(WorldBorderDrawer())
        multi_sub.sub(GridDrawer())

        # cell overlay
        multi_sub.sub(CellInfo(client))

        # HUD
        multi_sub.sub(Minimap())
        multi_sub.sub(Leaderboard())
        multi_sub.sub(MassGraph(client))
        multi_sub.sub(FpsMeter(50, Gdk.KEY_F3))

        client.player.nick = nick
        client.connect(address, token)

        gtk_watch_client(client)

        self.world_viewer = wv = WorldViewer(client.world)
        wv.draw_subscriber = wv.input_subscriber = multi_sub
        wv.focus_client(client)

    def on_world_update_post(self):
        self.world_viewer.drawing_area.queue_draw()

    def on_key_pressed(self, val, char):
        if char == 'q' or val == Gdk.KEY_Escape:
            self.client.disconnect()
            Gtk.main_quit()
        elif char == 's':
            self.client.send_spectate()
        elif char == 'r':
            self.client.send_respawn()
        elif char == 'c':  # reconnect to any server
            self.client.disconnect()
            address, token, *_ = find_server()
            self.client.connect(address, token)


def main():
    print("Copyright (C) 2015  Gjum  <code.gjum@gmail.com>\n"
          "This program comes with ABSOLUTELY NO WARRANTY.\n"
          "This is free software, and you are welcome to redistribute it\n"
          "under certain conditions; see LICENSE.txt for details.\n")

    if len(sys.argv) > 1 and sys.argv[1] in ('-h', '--help'):
        print("Usage: %s [nick]" % sys.argv[0])
        print("       %s party <token> [nick]" % sys.argv[0])
        print("       %s <IP:port> <token> [nick]" % sys.argv[0])
        return

    address, token, nick, *_ = sys.argv[1:] + ([None] * 3)

    if token is None:
        nick = address
        address = None

    if address and address[0] in 'Pp':
        address, *_ = get_party_address(token)

    if not address:
        address, token, *_ = find_server()

    GtkControl(address, token, nick)
    gtk_main_loop()
