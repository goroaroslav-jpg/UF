import unittest,ipaddress
from email.message import Message
from server import client_ip
class ProxyTests(unittest.TestCase):
 def header(self,*values):
  h=Message()
  for v in values:h['X-UF-Client-IP']=v
  h['X-Forwarded-For']='1.1.1.1'
  return h
 def test_untrusted_client_cannot_choose_ip(self):self.assertEqual(client_ip('198.51.100.4',self.header('8.8.8.8')), '198.51.100.4')
 def test_only_configured_proxy_accepted(self):
  trusted=(ipaddress.ip_network('172.30.88.2/32'),)
  self.assertEqual(client_ip('172.30.88.2',self.header('198.51.100.4'),trusted),'198.51.100.4')
  self.assertEqual(client_ip('172.30.88.3',self.header('198.51.100.4'),trusted),'172.30.88.3')
 def test_malformed_and_duplicate_headers_fall_back(self):
  trusted=(ipaddress.ip_network('127.0.0.1/32'),)
  for values in [('evil',),('1.1.1.1,2.2.2.2',),('8.8.8.8','1.1.1.1'),()]:self.assertEqual(client_ip('127.0.0.1',self.header(*values),trusted),'127.0.0.1')
 def test_ipv6_supported(self):self.assertEqual(client_ip('::1',self.header('2001:db8::1'),(ipaddress.ip_network('::1/128'),)),'2001:db8::1')
