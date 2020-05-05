# ############################################################################### #
# Autoreduction Repository : https://github.com/ISISScientificComputing/autoreduce
#
# Copyright &copy; 2020 ISIS Rutherford Appleton Laboratory UKRI
# SPDX - License - Identifier: GPL-3.0-or-later
# ############################################################################### #
"""
Constructs a plot and DashApp object for insertion into directly into a web page
"""
import logging

# Internal Dependencies
from plotting.plot_meta_language.interpreter import Interpreter

class Layout:
    """ Extract Layout as dictionary from interpreted meta data """
    def __init__(self, plot_style):
        """
        # Layout Properties

        :param plot_style (dictionary)
        """
        self.meta_data = plot_style
        self.mode = None
        self.plot_type = None
        self.error_bars = None
        self.layout = self.extract_layout()

    def _read_plot_meta_data(self):
        """
        Use plot interpreter to interpret plot meta data

        :return: interpreted_layout (dictionary)
        """
        try:
            interpreted_layout = Interpreter().interpret(self.meta_data)
            return interpreted_layout
        except ImportError:
            logging.error(f"Could not Interpret: {self.meta_data}")

    def _extract_layout(self):
        """
        Extracts plot layout data from plot style meta data

        :return self.meta_data (dictionary)
        """
        interpreted_layout = self._read_plot_meta_data()

        if 'mode' in interpreted_layout:
            self.mode = interpreted_layout.pop('mode')
        if 'plot' in interpreted_layout:
            self.plot_type = interpreted_layout.pop('plot')
        if 'error_bars' in interpreted_layout:
            self.error_bars = interpreted_layout.pop('error_bars')
        return interpreted_layout
