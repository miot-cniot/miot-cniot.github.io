import sys
import traceback
import os
import json

from os import path
from threading import Timer
from time import sleep

import config_utils
import IPCUtils as ipc_utils
import prediction_utils

import awsiot.greengrasscoreipc
import awsiot.greengrasscoreipc.client as client
from awsiot.greengrasscoreipc.model import (
    SubscribeToTopicRequest,
    SubscriptionResponseMessage,
    UnauthorizedError
)

# topic handler
class InferenceRequestHandler(client.SubscribeToTopicStreamHandler):

    def __init__(self):
        super().__init__()

    def on_stream_event(self, event: SubscriptionResponseMessage) -> None:
        try:
            message = json.loads(str(event.binary_message.message, "utf-8"))
            if 'image' in message:
                # Handle message.
                image = message['image']
                config_utils.logger.info(
                    "Received message: {}".format(image)
                )
                # Adds the image name to the default configuration
                config=ipc_utils.IPCUtils().get_configuration()
                config["ImageName"]=image
                # Run inference through a change in configuration
                set_configuration(config)
            else:  
                config_utils.logger.error("Invalid message format.")
                
        except:
            traceback.print_exc()

    def on_stream_error(self, error: Exception) -> bool:
        config_utils.logger.error("Received a stream error")
        traceback.print_exc()
        return False  # Return True to close stream, False to keep stream open.

    def on_stream_closed(self) -> None:
        print('Subscribe to topic stream closed.')


def set_configuration(config):
    r"""
    Sets a new config object with the combination of updated and default configuration as applicable.
    Calls inference code with the new config and indicates that the configuration changed.
    """
    new_config = {}

    if "ImageName" in config:
        new_config["image_name"] = config["ImageName"]
    else:
        new_config["image_name"] = config_utils.DEFAULT_IMAGE_NAME
        config_utils.logger.warning(
            "Using default image name: {}".format(config_utils.DEFAULT_IMAGE_NAME)
        )

    if "ImageDirectory" in config:
        new_config["image_dir"] = config["ImageDirectory"]
    else:
        new_config["image_dir"] = config_utils.IMAGE_DIR
        config_utils.logger.warning(
            "Using default image directory: {}".format(config_utils.IMAGE_DIR)
        )

    if "InferenceInterval" in config:
        new_config["prediction_interval_secs"] = config["InferenceInterval"]
    else:
        new_config["prediction_interval_secs"] = config_utils.DEFAULT_PREDICTION_INTERVAL_SECS
        config_utils.logger.warning(
            "Using default inference interval: {}".format(
                config_utils.DEFAULT_PREDICTION_INTERVAL_SECS
            )
        )

    if "UseCamera" in config:
        new_config["use_camera"] = config["UseCamera"]
    else:
        new_config["use_camera"] = config_utils.DEFAULT_USE_CAMERA
        config_utils.logger.warning(
            "Using default camera: {}".format(config_utils.DEFAULT_USE_CAMERA)
        )

    if "PublishResultsOnTopic" in config:
        config_utils.TOPIC = config["PublishResultsOnTopic"]
    else:
        config_utils.TOPIC = ""
        config_utils.logger.warning("Topic to publish inference results is empty.")

    if new_config["use_camera"].lower() == "true":
        prediction_utils.enable_camera()

    new_config["image"] = prediction_utils.load_image(
        path.join(new_config["image_dir"], new_config["image_name"])
    )
    # Run inference with the updated config indicating the config change.
    run_inference(new_config, True)


def run_inference(new_config, config_changed):
    r"""
    Uses the new config to run inference.

    :param new_config: Updated config if the config changed. Else, the last updated config.
    :param config_changed: Is True when run_inference is called after setting the newly updated config.
    Is False if run_inference is called using scheduled thread as the config hasn't changed.
    """

    if config_changed:
        if config_utils.SCHEDULED_THREAD is not None:
            config_utils.SCHEDULED_THREAD.cancel()
        config_changed = False
    if new_config["use_camera"].lower() == "true":
        prediction_utils.predict_from_cam()
    else:
        prediction_utils.predict_from_image(new_config["image"])
    config_utils.SCHEDULED_THREAD = Timer(
        int(new_config["prediction_interval_secs"]), run_inference, [new_config, config_changed]
    )
    config_utils.SCHEDULED_THREAD.start()


# Get intial configuration from the recipe and run inference for the first time.
set_configuration(ipc_utils.IPCUtils().get_configuration())

# Subscribe to the subsequent configuration changes
ipc_utils.IPCUtils().get_config_updates()

# Local topic configuration
topic = str(os.environ['TOPIC'])
ipc_client = awsiot.greengrasscoreipc.connect()
request = SubscribeToTopicRequest()
request.topic = topic
handler = InferenceRequestHandler()
operation = ipc_client.new_subscribe_to_topic(handler)
operation.activate(request)
future_response = operation.get_response()

# Keeps checking for the updated_config value every one second. If the config changes, it's `True` and
# inference will be run with the updated config (run_inference) after setting the new config(set_configuration).
# Toggle it back to `False` and look out for the config updates.
while True:
    if config_utils.UPDATED_CONFIG:
        set_configuration(ipc_utils.IPCUtils().get_configuration())
        config_utils.UPDATED_CONFIG = False
    sleep(1)
    
# To stop subscribing, close the operation stream.
operation.close()