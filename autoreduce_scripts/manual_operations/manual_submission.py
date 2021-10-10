# ############################################################################### #
# Autoreduction Repository : https://github.com/ISISScientificComputing/autoreduce
#
# Copyright &copy; 2020 ISIS Rutherford Appleton Laboratory UKRI
# SPDX - License - Identifier: GPL-3.0-or-later
# ############################################################################### #
"""
A module for creating and submitting manual submissions to autoreduction
"""
from __future__ import print_function

import sys
from typing import List, Tuple, Union
import logging
import traceback

import fire
import h5py

from autoreduce_scripts.manual_operations import setup_django

setup_django()

from autoreduce_db.reduction_viewer.models import ReductionRun

from autoreduce_utils.clients.connection_exception import ConnectionException
from autoreduce_utils.clients.icat_client import ICATClient
from autoreduce_utils.clients.queue_client import QueueClient
from autoreduce_utils.clients.tools.isisicat_prefix_mapping import get_icat_instrument_prefix
from autoreduce_utils.message.message import Message

from autoreduce_scripts.manual_operations.rb_categories import RBCategory
from autoreduce_scripts.manual_operations.util import get_run_range

logger = logging.getLogger(__file__)


def submit_run(active_mq_client,
               rb_number: Union[str, List[str]],
               instrument: str,
               data_file_location: Union[str, List[str]],
               run_number: Union[int, Tuple[int]],
               reduction_arguments: dict = None,
               user_id=-1,
               description=""):
    """
    Submit a new run for autoreduction
    :param active_mq_client: The client for access to ActiveMQ
    :param rb_number: desired experiment rb number
    :param instrument: name of the instrument
    :param data_file_location: location of the data file
    :param run_number: run number fo the experiment
    """
    if active_mq_client is None:
        raise RuntimeError("ActiveMQ not connected, cannot submit runs")

    message = Message(rb_number=rb_number,
                      instrument=instrument,
                      data=data_file_location,
                      run_number=run_number,
                      facility="ISIS",
                      started_by=user_id,
                      reduction_arguments=reduction_arguments,
                      description=description)
    active_mq_client.send('/queue/DataReady', message, priority=1)
    logger.info("Submitted run: %s", message.serialize(indent=1))
    return message.to_dict()


def get_location_and_rb_from_database(instrument, run_number) -> Union[None, Tuple[str, str]]:
    """
    Retrieves a run's data-file location and rb_number from the auto-reduction database
    :param database_client: Client to access auto-reduction database
    :param instrument: (str) the name of the instrument associated with the run
    :param run_number: The run number of the data to be retrieved
    :return: The data file location and rb_number, or None if this information is not
    in the database
    """
    reduction_run_record = ReductionRun.objects.filter(
        instrument__name=instrument, run_numbers__run_number=run_number).order_by('run_version').first()

    if not reduction_run_record:
        return None

    data_location = reduction_run_record.data_location.first().file_path
    experiment_number = str(reduction_run_record.experiment.reference_number)

    return data_location, experiment_number


def icat_datafile_query(icat_client, file_name):
    """
    Search for file name in icat and return it if it exist.
    :param icat_client: Client to access the ICAT service
    :param file_name: file name to search for in icat
    :return: icat datafile entry if found
    :raises SystemExit: If icat_client not connected
    """
    if icat_client is None:
        raise RuntimeError("ICAT not connected")

    return icat_client.execute_query("SELECT df FROM Datafile df WHERE df.name = '" + file_name +
                                     "' INCLUDE df.dataset AS ds, ds.investigation")


def get_location_and_rb_from_icat(instrument, run_number, file_ext) -> Tuple[str, str]:
    """
    Retrieves a run's data-file location and rb_number from ICAT.
    Attempts first with the default file name, then with prepended zeroes.
    :param icat_client: Client to access the ICAT service
    :param instrument: The name of instrument
    :param run_number: The run number to be processed
    :param file_ext: The expected file extension
    :return: The data file location and rb_number
    :raises SystemExit: If the given run information cannot return a location and rb_number
    """
    icat_client = login_icat()

    # look for file-name assuming file-name uses prefix instrument name
    icat_instrument_prefix = get_icat_instrument_prefix(instrument)
    file_name = f"{icat_instrument_prefix}{str(run_number).zfill(5)}.{file_ext}"
    datafile = icat_datafile_query(icat_client, file_name)

    if not datafile:
        print("Cannot find datafile '" + file_name + "' in ICAT. Will try with zeros in front of run number.")
        file_name = f"{icat_instrument_prefix}{str(run_number).zfill(8)}.{file_ext}"
        datafile = icat_datafile_query(icat_client, file_name)

    # look for file-name assuming file-name uses full instrument name
    if not datafile:
        print("Cannot find datafile '" + file_name + "' in ICAT. Will try using full instrument name.")
        file_name = f"{instrument}{str(run_number).zfill(5)}.{file_ext}"
        datafile = icat_datafile_query(icat_client, file_name)

    if not datafile:
        print("Cannot find datafile '" + file_name + "' in ICAT. Will try with zeros in front of run number.")
        file_name = f"{instrument}{str(run_number).zfill(8)}.{file_ext}"
        datafile = icat_datafile_query(icat_client, file_name)

    if not datafile:
        raise RuntimeError("Cannot find datafile '" + file_name + "' in ICAT.")
    return datafile[0].location, datafile[0].dataset.investigation.name


def overwrite_icat_calibration_rb_num(location: str, rb_num: Union[str, int]) -> str:
    """Checks if the RB number provided has been overwritten by ICAT as a calibration run.
    If so it returns the real RB number read from the datafile.
    """
    rb_num = str(rb_num)

    if "CAL" in rb_num:
        rb_num = _read_rb_from_datafile(location)

    return rb_num


def get_location_and_rb(instrument, run_number, file_ext):
    """
    Retrieves a run's data-file location and rb_number from the auto-reduction database,
    or ICAT (if it is not in the database)
    :param database_client: Client to access auto-reduction database
    :param icat_client: Client to access the ICAT service
    :param instrument: The name of instrument
    :param run_number: The run number to be processed
    :param file_ext: The expected file extension
    :return: The data file location and rb_number
    :raises SystemExit: If the given run information cannot return a location and rb_number
    """
    try:
        run_number = int(run_number)
    except ValueError:
        print(f"Cannot cast run_number as an integer. Run number given: '{run_number}'. Exiting...")
        sys.exit(1)

    result = get_location_and_rb_from_database(instrument, run_number)
    if result:
        return result
    print(f"Cannot find datafile for run_number {run_number} in Auto-reduction database. " f"Will try ICAT...")

    location, rb_num = get_location_and_rb_from_icat(instrument, run_number, file_ext)
    rb_num = overwrite_icat_calibration_rb_num(location, rb_num)
    return location, rb_num


def login_icat():
    """
    Log into the ICATClient
    :return: The client connected, or None if failed
    """
    print("Logging into ICAT")
    icat_client = ICATClient()
    try:
        icat_client.connect()
    except ConnectionException as exc:
        print("Couldn't connect to ICAT. Continuing without ICAT connection.")
        raise RuntimeError("Unable to proceed. Unable to connect to ICAT.") from exc
    return icat_client


def login_queue():
    """
    Log into the QueueClient
    :return: The client connected, or raise exception
    """
    print("Logging into ActiveMQ")
    activemq_client = QueueClient()
    try:
        activemq_client.connect()
    except (ConnectionException, ValueError) as exp:
        raise RuntimeError(
            "Cannot connect to ActiveMQ with provided credentials in credentials.ini\n"
            "Check that the ActiveMQ service is running, and the username, password and host are correct.") from exp
    return activemq_client


def _read_rb_from_datafile(location: str):
    """
    Reads the RB number from the location of the datafile
    """
    def windows_to_linux_path(path):
        """ Convert windows path to linux path.
        :param path:
        :param temp_root_directory:
        :return: (str) linux formatted file path
        """
        # '\\isis\inst$\' maps to '/isis/'
        path = path.replace('\\\\isis\\inst$\\', '/isis/')
        path = path.replace('\\', '/')
        return path

    location = windows_to_linux_path(location)
    try:
        nxs_file = h5py.File(location, mode="r")
    except OSError as err:
        raise RuntimeError(f"Cannot open file '{location}'") from err

    for (_, entry) in nxs_file.items():
        try:
            return str(entry.get('experiment_identifier')[:][0].decode("utf-8"))
        except Exception as err:
            raise RuntimeError("Could not read RB number from datafile") from err
    raise RuntimeError(f"Datafile at {location} does not have any items that can be iterated")


def categorize_rb_number(rb_num: str):
    """
    Map RB number to a category. If an ICAT calibration RB number is provided,
    the datafile will be checked to find out the real experiment number.

    This is because ICAT will overwrite the real RB number for calibration runs!
    """
    if len(rb_num) != 7:
        return RBCategory.UNCATEGORIZED

    if rb_num[2] == "0":
        return RBCategory.DIRECT_ACCESS
    elif rb_num[2] in ["1", "2"]:
        return RBCategory.RAPID_ACCESS
    elif rb_num[2] == "3" and rb_num[3] == "0":
        return RBCategory.COMMISSIONING
    elif rb_num[2] == "3" and rb_num[3] == "5":
        return RBCategory.CALIBRATION
    elif rb_num[2] == "5":
        return RBCategory.INDUSTRIAL_ACCESS
    elif rb_num[2] == "6":
        return RBCategory.INTERNATIONAL_PARTNERS
    elif rb_num[2] == "9":
        return RBCategory.XPESS_ACCESS
    else:
        return RBCategory.UNCATEGORIZED


# def get_script_and_arguments(instrument: str, arguments: Optional[dict]) -> Tuple[str, dict, str]:
#     """
#     Loads the reduction script (reduce.py) as a string, and if arguments are not provided it loads
#     them from reduce_vars.py as a module, which is then converted to a dictionary.

#     Args:
#         instrument: The name of the instrument for which the scripts will be loaded
#         arguments: The reduction arguments that will be used for the reduction.
#                    If None, the default arguments will be loaded from reduce_vars.py

#     Returns:
#         The reduction script as a string, the reduction arguments as a dictionary,
#         and any error messages encountered
#     """
#     error_message = ""
#     scripts_dir = Path(SCRIPTS_DIRECTORY % instrument)
#     try:
#         reduce_path = scripts_dir / "reduce.py"
#         with io.open(reduce_path, 'r') as open_file:
#             script = open_file.read()
#     except IOError:
#         script = ""

#     if not arguments:
#         arguments = {
#             "standard_vars": {},
#             "advanced_vars": {},
#             "variable_help": {
#                 "standard_vars": {},
#                 "advanced_vars": {}
#             }
#         }
#         vars_path = scripts_dir / "reduce_vars.py"
#         try:
#             spec = spec_from_file_location("reduce_vars.py", vars_path)
#             if spec is None:
#                 raise ImportError(f"Module at {vars_path} does not exist.")
#             module = module_from_spec(spec)
#             spec.loader.exec_module(module)
#             for dict_name in ["standard_vars", "advanced_vars", "variable_help"]:
#                 arguments[dict_name] = getattr(module, dict_name, {})

#         except ImportError as exc:
#             error_message = f"Unable to load reduction script {vars_path} due to missing import. ({exc})"
#             logger.error(error_message)
#         except SyntaxError as exc:
#             error_message = f"Syntax error in reduction script {vars_path}"
#             logger.error(error_message)

#     return script, arguments, error_message


def main(instrument, runs, reduction_script=None, reduction_arguments=None, user_id=-1, description=""):
    """
    Manually submit an instrument run from reduction.
    All run number between `first_run` and `last_run` are submitted.

    :param instrument: (string) The name of the instrument to submit a run for
    :param first_run: (int) The first run to be submitted
    :param last_run: (int) The last run to be submitted
    :param reduction_arguments: (dict) The arguments to be passed to the reduction script,
                                if None the reduce_vars.py file will be loaded
    """

    instrument = instrument.upper()

    activemq_client = login_queue()

    submitted_runs = []

    for run_number in runs:
        location, rb_num = get_location_and_rb(instrument, run_number, "nxs")
        try:
            category = categorize_rb_number(rb_num)
            logger.info("Run is in category %s", category)
        except RuntimeError:
            logger.warning("Could not categorize the run due to an invalid RB number. It will be not be submitted.\n%s",
                           traceback.format_exc())
            continue

        if location and rb_num is not None:
            submitted_runs.append(
                submit_run(activemq_client, rb_num, instrument, location, run_number, reduction_arguments, user_id,
                           description))
        else:
            logger.error("Unable to find RB number and location for %s%s", instrument, run_number)

    return submitted_runs


def fire_entrypoint():
    """
    Entrypoint into the Fire CLI interface. Used via setup.py console_scripts
    """
    fire.Fire(main)  # pragma: no cover


if __name__ == "__main__":
    fire.Fire(main)  # pragma: no cover
