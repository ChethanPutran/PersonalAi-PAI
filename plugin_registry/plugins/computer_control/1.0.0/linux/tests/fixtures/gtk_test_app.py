import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
w = Gtk.Window(title="PAI Test App")
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6); w.add(box)
entry = Gtk.Entry(); entry.set_placeholder_text("Search"); box.add(entry)
lbl = Gtk.Label(label="Status: idle"); box.add(lbl)
btn = Gtk.Button(label="Say hello")
btn.connect("clicked", lambda b: lbl.set_text("Status: clicked"))
box.add(btn)
chk = Gtk.CheckButton(label="Enable feature"); box.add(chk)
w.connect("destroy", Gtk.main_quit); w.show_all()
GLib.timeout_add_seconds(60, Gtk.main_quit)
Gtk.main()
