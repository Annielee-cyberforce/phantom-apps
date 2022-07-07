# File: paloaltocortexxdr_connector.py
#
# Licensed under Apache 2.0 (https://www.apache.org/licenses/LICENSE-2.0.txt)
#

# Phantom App imports
import phantom.app as phantom
from phantom.base_connector import BaseConnector
from phantom.action_result import ActionResult

# Usage of the consts file is recommended
from reaqta_consts import *
import requests
import json
from bs4 import BeautifulSoup
import sys

from datetime import datetime, timezone, timedelta

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class RetVal(tuple):
    def __new__(cls, val1, val2=None):
        return tuple.__new__(RetVal, (val1, val2))

class TestConnector(BaseConnector):

    def __init__(self):

        # Call the BaseConnectors init first
        super(TestConnector, self).__init__()
        
        self._base_url = None
        self._state = None
        self._secret_key =None
        self._app_id = None

        self._token=None
        self._token_expire = int(datetime.now(timezone.utc).timestamp())

    
    def initialize(self):

        # Load the state in initialize, use it to store data
        # that needs to be accessed across actions
        self._state = self.load_state()

        # get the asset config
        config = self.get_config()

        self._base_url="https://bcp.ap.rqt.io/rqt-api"
        self._secret_key = config['secret_key']
        self._app_id = config['app_id']
        self._verify = config.get('verify_server_cert', False)
    
        return phantom.APP_SUCCESS

    def finalize(self):
        # Save the state, this data is saved across actions and app upgrades
        self.save_state(self._state)
        return phantom.APP_SUCCESS

    def _get_error_message_from_exception(self, e):
        """ This method is used to get appropriate error messages from the exception.
        :param e: Exception object
        :return: error message
        """

        try:
            if e.args:
                if len(e.args) > 1:
                    error_code = e.args[0]
                    error_msg = e.args[1]
                elif len(e.args) == 1:
                    error_code = ERR_CODE_MSG
                    error_msg = e.args[0]
            else:
                error_code = ERR_CODE_MSG
                error_msg = ERR_MSG_UNAVAILABLE
        except:
            error_code = ERR_CODE_MSG
            error_msg = ERR_MSG_UNAVAILABLE

        try:
            if error_code in ERR_CODE_MSG:
                error_text = "Error Message: {0}".format(error_msg)
            else:
                error_text = "Error Code: {0}. Error Message: {1}".format(error_code, error_msg)
        except:
            self.debug_print(PARSE_ERR_MSG)
            error_text = PARSE_ERR_MSG

        return error_text

    def _validate_integer(self, action_result, parameter, key):
        if parameter is not None:
            try:
                if not float(parameter).is_integer():
                    return action_result.set_status(phantom.APP_ERROR, VALID_INTEGER_MSG.format(key=key)), None

                parameter = int(parameter)
            except:
                return action_result.set_status(phantom.APP_ERROR, VALID_INTEGER_MSG.format(key=key)), None

            if parameter < 0:
                return action_result.set_status(phantom.APP_ERROR, NON_NEGATIVE_INTEGER_MSG.format(key=key)), None

        return phantom.APP_SUCCESS, parameter

    def _process_empty_response(self, response, action_result):
        if response.status_code == 200:
            return RetVal(phantom.APP_SUCCESS, {})

        return RetVal(
            action_result.set_status(
                phantom.APP_ERROR, "Status code: {0}. Empty response and no information in the header".format(response.status_code)
            ), None
        )

    def _process_html_response(self, response, action_result):
        # An html response, treat it like an error
        status_code = response.status_code

        try:
            soup = BeautifulSoup(response.text, "html.parser")
            for element in soup(["script", "style", "footer", "nav"]):
               element.extract()
            error_text = soup.text
            split_lines = error_text.split('\n')
            split_lines = [x.strip() for x in split_lines if x.strip()]
            error_text = '\n'.join(split_lines)
        except:
            error_text = "Cannot parse error details"

        message = "Status Code: {0}. Data from server:\n{1}\n".format(status_code, error_text)

        message = message.replace('{', '{{').replace('}', '}}')
        return RetVal(action_result.set_status(phantom.APP_ERROR, message), None)

    def _process_json_response(self, r, action_result):
        # Try a json parse
        try:
            resp_json = r.json()
        except Exception as e:
            err = self._get_error_message_from_exception(e)
            error_message = "Unable to parse JSON response. Error: {0}".format(err)
            return RetVal(action_result.set_status(phantom.APP_ERROR, error_message), None)

        # Please specify the status codes here
        if 200 <= r.status_code < 399:
            return RetVal(phantom.APP_SUCCESS, resp_json)

        # You should process the error returned in the json
        error_message = r.text.replace('{', '{{').replace('}', '}}')
        message = "Error from server. Status Code: {0} Data from server: {1}".format(
            r.status_code, error_message)

        return RetVal(action_result.set_status(phantom.APP_ERROR, message), None)

    def _process_response(self, r, action_result):
        # store the r_text in debug data, it will get dumped in the logs if the action fails
        if hasattr(action_result, 'add_debug_data'):
            action_result.add_debug_data({'r_status_code': r.status_code})
            action_result.add_debug_data({'r_text': r.text})
            action_result.add_debug_data({'r_headers': r.headers})

        # Process each 'Content-Type' of response separately

        # Process a json response
        if 'json' in r.headers.get('Content-Type', ''):
            return self._process_json_response(r, action_result)

        # Process an HTML response, Do this no matter what the api talks.
        # There is a high chance of a PROXY in between phantom and the rest of
        # world, in case of errors, PROXY's return HTML, this function parses
        # the error and adds it to the action_result.
        if 'html' in r.headers.get('Content-Type', ''):
            return self._process_html_response(r, action_result)

        # it's not content-type that is to be parsed, handle an empty response
        if not r.text:
            return self._process_empty_response(r, action_result)

        # everything else is actually an error at this point
        message = "Can't process response from server. Status Code: {0} Data from server: {1}".format(
            r.status_code,
            r.text.replace('{', '{{').replace('}', '}}')
        )

        return RetVal(action_result.set_status(phantom.APP_ERROR, message), None)

    
    def _get_token(self,action_result,method="post"):
        
        resp_json = None
        headers = {
            "secret": str(self._secret_key),
            "id": str(self._app_id)
        }

        authenticate_url= '/1/authenticate/'
        url = "{0}{1}".format(self._base_url, authenticate_url)
        print(url)

        try:
            request_func = getattr(requests, method)
        except AttributeError:
             return RetVal(
                action_result.set_status(phantom.APP_ERROR, "Invalid method: {0}".format(method)),
                resp_json
            )

        try:
            r = request_func(
                url,
                verify=self._verify,
                json=headers
            )
        except requests.exceptions.InvalidURL:
            error_message = "Error connecting to server. Invalid URL %s" % (url)
            return RetVal(action_result.set_status(phantom.APP_ERROR, error_message), resp_json)
        except requests.exceptions.ConnectionError:
            error_message = "Error connecting to server. Connection Refused from the Server for %s" % (url)
            return RetVal(action_result.set_status(phantom.APP_ERROR, error_message), resp_json)
        except requests.exceptions.InvalidSchema:
            error_message = "Error connecting to server. No connection adapters were found for %s" % (url)
            return RetVal(action_result.set_status(phantom.APP_ERROR, error_message), resp_json)
        except Exception as e:
            err = self._get_error_message_from_exception(e)
            error_message = "Error Connecting to server. {0}".format(err)
            return RetVal(action_result.set_status(phantom.APP_ERROR, error_message), resp_json)
  
        return self._process_response(r, action_result)
        

    def _make_rest_call(self,url,action_result,method="get",**kwargs):
        # **kwargs can be any additional parameters that requests.request accepts
        resp_json = None

        try:
            request_func = getattr(requests, method)
        except AttributeError:
            return RetVal(
                action_result.set_status(phantom.APP_ERROR, "Invalid method: {0}".format(method)),
                resp_json
            )

        url = "{0}{1}".format(self._base_url, url)
        print(url)


        try:
            r = request_func(
                url,
                verify=self._verify,
                **kwargs
            )
        except requests.exceptions.InvalidURL:
            error_message = "Error connecting to server. Invalid URL %s" % (url)
            return RetVal(action_result.set_status(phantom.APP_ERROR, error_message), resp_json)
        except requests.exceptions.ConnectionError:
            error_message = "Error connecting to server. Connection Refused from the Server for %s" % (url)
            return RetVal(action_result.set_status(phantom.APP_ERROR, error_message), resp_json)
        except requests.exceptions.InvalidSchema:
            error_message = "Error connecting to server. No connection adapters were found for %s" % (url)
            return RetVal(action_result.set_status(phantom.APP_ERROR, error_message), resp_json)
        except Exception as e:
            err = self._get_error_message_from_exception(e)
            error_message = "Error Connecting to server. {0}".format(err)
            return RetVal(action_result.set_status(phantom.APP_ERROR, error_message), resp_json)
        
        return self._process_response(r, action_result)

    def _handle_test_connectivity(self, param):
        # Add an action result object to self (BaseConnector) to represent the action for this param
        action_result = self.add_action_result(ActionResult(dict(param)))  

        self.save_progress("Connecting to API server")  
        
        ret_val, authorization = self._get_token()
        #print(authorization)
        self._token=authorization["token"]
        self._token_expire=authorization["expiresAt"]
        print("token updated!")

        if phantom.is_fail(ret_val):
            # the call to the 3rd party device or service failed, action result should contain all the error details
            self.save_progress("Test Connectivity Failed")
            return action_result.get_status()
        
        # Return success
        self.save_progress("Test Connectivity Passed")
        return action_result.set_status(phantom.APP_SUCCESS)

    def _handle_get_alert(self,param):

       # use self.save_progress(...) to send progress messages back to the platform
        self.save_progress("In action handler for: {0}".format(self.get_action_identifier()))

        # Add an action result object to self (BaseConnector) to represent the action for this param
        action_result = self.add_action_result(ActionResult(dict(param)))
        
        # Access action parameters passed in the 'param' dictionary
        alert_id=param.get("alert_id")
        timestamp_now = int(datetime.now(timezone.utc).timestamp())

        if (timestamp_now <=self._token_expire):
            ret_val,authorization = self._get_token()
            #print(authorization)
            
            if phantom.is_fail(ret_val):
                # the call to the 3rd party device or service failed, action result should contain all the error details
                self.save_progress("Test Connectivity Failed")
                return action_result.get_status()

            self._token=authorization["token"]
            self._token_expire=authorization["expiresAt"]
            print("token updated!")
    
        token_authorization = {'Authorization': 'Bearer {}'.format(self._token)}
        get_alert_url = "{0}{1}".format('/1/alert/',alert_id)    
        ret_val, response = self._make_rest_call(get_alert_url,headers=token_authorization)
        #print(response)

        if phantom.is_fail(ret_val):
            # the call to the 3rd party device or service failed, action result should contain all the error details
            return action_result.get_status()

        # Add the response into the data section
        action_result.add_data(response)
        self.save_progress("Response JSON: {0}".format(response))

        try:
            # Add a dictionary that is made up of the most important values from data into the summary
            summary = action_result.update_summary({})
            summary["alertStatus"] = response["alertStatus"]
            summary["raw"] = response
        except Exception:
            self.debug_print(ERR_PARSING_RESPONSE)

        # Return success, no need to set the message, only the status
        # BaseConnector will create a textual message based off of the summary dictionary
        return action_result.set_status(phantom.APP_SUCCESS)

    
    def _handle_get_alert_with_localid(self,param):

        # use self.save_progress(...) to send progress messages back to the platform
        self.save_progress("In action handler for: {0}".format(self.get_action_identifier()))

        # Add an action result object to self (BaseConnector) to represent the action for this param
        action_result = self.add_action_result(ActionResult(dict(param)))

        # Access action parameters passed in the 'param' dictionary
        endpointid=param.get("endpointId")
        localid=param.get("localId")

        # Validate parameter
        ret_val, endpointid = self._validate_integer(action_result, endpointid, ENDPOINTID_ACTION_PARAM)
        if phantom.is_fail(ret_val):
            return action_result.get_status()
        ret_val, localid = self._validate_integer(action_result, localid, LOCALID_ACTION_PARAM)
        if phantom.is_fail(ret_val):
            return action_result.get_status()

        timestamp_now = int(datetime.now(timezone.utc).timestamp())

        if (timestamp_now <=self._token_expire):
            ret_val,authorization = self._get_token()
            #print(authorization)

            if phantom.is_fail(ret_val):
                # the call to the 3rd party device or service failed, action result should contain all the error details
                self.save_progress("Test Connectivity Failed")
                return action_result.get_status()

            self._token=authorization["token"]
            self._token_expire=authorization["expiresAt"]
            print("token updated!")
    
        token_authorization = {'Authorization': 'Bearer {}'.format(self._token)}
        get_alert_url = "{0}{1}{2}{3}".format('/1/alert/local/',localid,'/endpoint/',endpointid)    
        ret_val,response = self._make_rest_call(get_alert_url,headers=token_authorization)
        #print(response)

        if phantom.is_fail(ret_val):
            # the call to the 3rd party device or service failed, action result should contain all the error details
            return action_result.get_status()

        # Add the response into the data section
        action_result.add_data(response)
        self.save_progress("Response JSON: {0}".format(response))

        try:
            # Add a dictionary that is made up of the most important values from data into the summary
            summary = action_result.update_summary({})
            summary['alertStatus'] = response["alertStatus"]      
            summary["raw"] = response
        except Exception:
            self.debug_print(ERR_PARSING_RESPONSE)

        # Return success, no need to set the message, only the status
        # BaseConnector will create a textual message based off of the summary dictionary
        return action_result.set_status(phantom.APP_SUCCESS)


    def _handle_close_alert(self,param):

        # use self.save_progress(...) to send progress messages back to the platform
        self.save_progress("In action handler for: {0}".format(self.get_action_identifier()))

        # Add an action result object to self (BaseConnector) to represent the action for this param
        action_result = self.add_action_result(ActionResult(dict(param)))
        
        parameters={}
        # Access action parameters passed in the 'param' dictionary
        alert_id=param.get("alert_id")
        parameters["malicious"]=param.get("malicious")

        timestamp_now = int(datetime.now(timezone.utc).timestamp())

        if (timestamp_now <=self._token_expire):
            ret_val,authorization = self._get_token()
            #print(authorization)
            
            if phantom.is_fail(ret_val):
                # the call to the 3rd party device or service failed, action result should contain all the error details
                self.save_progress("Test Connectivity Failed")
                return action_result.get_status()
            
            self._token=authorization["token"]
            self._token_expire=authorization["expiresAt"]
            print("token update!")
    
        token_authorization = {'Authorization': 'Bearer {}'.format(self._token)}
        close_alert_url = "{0}{1}{2}".format('/1/alert/',alert_id,'/close')    
        ret_val,response = self._make_rest_call(close_alert_url, method="post",headers=token_authorization,JSON=parameters)
        #print(response)

        if phantom.is_fail(ret_val):
            # the call to the 3rd party device or service failed, action result should contain all the error details
            return action_result.get_status()

        # Add the response into the data section
        action_result.add_data(response)
        self.save_progress("Response JSON: {0}".format(response))

        try:
            # Add a dictionary that is made up of the most important values from data into the summary
            summary = action_result.update_summary({})
            summary["raw"] = response
        except Exception:
            self.debug_print(ERR_PARSING_RESPONSE)

        # Return success, no need to set the message, only the status
        # BaseConnector will create a textual message based off of the summary dictionary
        return action_result.set_status(phantom.APP_SUCCESS)

    
    def _handle_search_alerts(self,param):

        # use self.save_progress(...) to send progress messages back to the platform
        self.save_progress("In action handler for: {0}".format(self.get_action_identifier()))

        # Add an action result object to self (BaseConnector) to represent the action for this param
        action_result = self.add_action_result(ActionResult(dict(param)))

        # Access action parameters passed in the 'param' dictionary
        parameters={}
        parameters["triggerCondition"]=param.get("triggerCondition")
        parameters["count"]=param.get("count")

        timestamp_now = int(datetime.now(timezone.utc).timestamp())
        #print(parameters)

        if (timestamp_now <=self._token_expire):
            ret_val,authorization = self._get_token()
            #print(authorization)

            if phantom.is_fail(ret_val):
                # the call to the 3rd party device or service failed, action result should contain all the error details
                self.save_progress("Test Connectivity Failed")
                return action_result.get_status()

            self._token=authorization["token"]
            self._token_expire=authorization["expiresAt"]
            print("token update!")

        token_authorization = {'Authorization': 'Bearer {}'.format(self._token)}
        ret_val,response = self._make_rest_call('/1/alerts/', headers=token_authorization,json=parameters)
        #print(response)

        if phantom.is_fail(ret_val):
            # the call to the 3rd party device or service failed, action result should contain all the error details
            return action_result.get_status()

        # Add the response into the data section
        action_result.add_data(response)
        self.save_progress("Response JSON: {0}".format(response))

        try:
            # Add a dictionary that is made up of the most important values from data into the summary
            summary = action_result.update_summary({})
            summary["raw"] = response
        except Exception:
            self.debug_print(ERR_PARSING_RESPONSE)

        # Return success, no need to set the message, only the status
        # BaseConnector will create a textual message based off of the summary dictionary
        return action_result.set_status(phantom.APP_SUCCESS)



    def _handle_get_policy(self,param):

        # use self.save_progress(...) to send progress messages back to the platform
        self.save_progress("In action handler for: {0}".format(self.get_action_identifier()))

        # Add an action result object to self (BaseConnector) to represent the action for this param
        action_result = self.add_action_result(ActionResult(dict(param)))

        policy_id=param.get("policy_id")

        timestamp_now = int(datetime.now(timezone.utc).timestamp())

        if (timestamp_now <=self._token_expire):
            ret_val,authorization = self._get_token()
            #print(authorization)

            if phantom.is_fail(ret_val):
                # the call to the 3rd party device or service failed, action result should contain all the error details
                self.save_progress("Test Connectivity Failed")
                return action_result.get_status()
            
            self._token=authorization["token"]
            self._token_expire=authorization["expiresAt"]
            print("token update!")
    
        token_authorization = {'Authorization': 'Bearer {}'.format(self._token)}
        get_policy_url='/1/policy/'
        get_policy_url = "{0}{1}".format(get_policy_url,policy_id)    
        ret_val,response = self._make_rest_call(get_policy_url, headers=token_authorization)
        #print(response)


        if phantom.is_fail(ret_val):
            # the call to the 3rd party device or service failed, action result should contain all the error details
            return action_result.get_status()

        # Add the response into the data section
        action_result.add_data(response)
        self.save_progress("Response JSON: {0}".format(response))

        try:
            # Add a dictionary that is made up of the most important values from data into the summary
            summary = action_result.update_summary({})
            summary["raw"] = response
        except Exception:
            self.debug_print(ERR_PARSING_RESPONSE)

        # Return success, no need to set the message, only the status
        # BaseConnector will create a textual message based off of the summary dictionary
        return action_result.set_status(phantom.APP_SUCCESS)

    def handle_action(self,param):

        ret_val = phantom.APP_SUCCESS

        # Get the action that we are supposed to execute for this App Run
        action_id = self.get_action_identifier()

        self.debug_print("action_id", self.get_action_identifier())

  
        if action_id == 'test_connectivity':
            ret_val = self._handle_test_connectivity(param)
        
        elif action_id == 'get_alert':
            ret_val = self._handle_get_alert(param)
                   
        elif action_id == 'get_alert_with_localid':
            ret_val = self._handle_get_alert_with_localid(param)

        elif action_id == 'close_alert':
            ret_val = self._handle_close_alert(param)

        elif action_id == 'search_alerts':
            ret_val = self._handle_search_alerts(param)
        
        elif action_id == 'get_policy':
            ret_val = self._handle_get_policy(param)


        return ret_val
    


def main():

    import pudb
    pudb.set_trace()
    with open(sys.argv[1]) as f:
        in_json = f.read()
        in_json = json.loads(in_json)
        print(json.dumps(in_json, indent=' ' * 4))
        connector=TestConnector()
        connector.print_progress_message = True
        ret_val = connector._handle_action(json.dumps(in_json), None)
        print(ret_val)
    exit(0)
    

if __name__ == '__main__':
    main()

