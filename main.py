"""
main.py
--------
Application entry point.

Run with:
    python main.py

Requirements:
    pip install -r requirements.txt
"""

import sys
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt

from database.db_manager import initialize_database
from ui.main_window import MainWindow


def main():
    # Step 1: Initialize the database (creates tables if they don't exist)
    initialize_database()

    # Step 2: Start the Qt application
    app = QApplication(sys.argv)
    app.setApplicationName("BESS Tracker")
    app.setStyle("Fusion")

    # Apply industrial theme
    from ui.style import APP_STYLE
    app.setStyleSheet(APP_STYLE)

    # Step 3: Create and show the main window
    window = MainWindow()
    window.show()

    # Step 4: Run the event loop
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
