# Quick Change Trader v0.91
import warnings
warnings.filterwarnings("ignore") # NOTE: used to ignore gencode errors (on my machine)
#from var_dump import var_dump     # used for quick debug of tws api classes, especially contracts

from ibapi.client import *
from ibapi.wrapper import *
from ibapi.order_condition import *

import random
import itertools
import time
import math
import signal

#WINDOWS
import win32gui, win32api # win api used to grab window focus on scroll, can comment out or replace
import win32con

from PyQt6.QtCore import QT_VERSION_STR
from PyQt6.QtCore import PYQT_VERSION_STR
print('qt info:', QT_VERSION_STR, PYQT_VERSION_STR)

from PyQt6.QtWidgets import QApplication, QWidget, QToolTip, QPushButton, QLineEdit, QListWidget, QListWidgetItem
from PyQt6.QtGui import QFont, QPainter, QColor, QStaticText, QFontMetrics, QTransform, QPixmap, QPainterPath, QImage, QRegularExpressionValidator, QPen, QIcon, QRadialGradient, QBrush, QLinearGradient, QCursor, QTextOption
from PyQt6.QtCore import Qt, QBasicTimer, pyqtSignal, QSize, QThread, QPoint, QPointF, pyqtSignal, pyqtSlot, QRect, QRectF, QRegularExpression, QTimer, QDateTime, QEvent

# TWS API DOCS
# tick types:
# https://interactivebrokers.github.io/tws-api/tick_types.html
# EWrapper functions:
# https://interactivebrokers.github.io/tws-api/interfaceIBApi_1_1EWrapper.html
# new docs with sparse python examples:
# https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/#establish-connection

# TWS classes redefs twsapiX
class twsapiWrapper(EWrapper):
    def __init__(self):
        self.open_order_req_ended = True

    def nextValidId(self, orderId):
        tws_Trade.valid_id = orderId

    # usually send data to qt6 event loop via slots
    # sometimes set vars directly when threading is not a concern

    def tickSize(self, tickerId, field, value):
        ladderex.tick_signal.emit(tickerId, field, value)

    def tickGeneric(self, tickerId, field, value):
        ladderex.tick_signal.emit(tickerId, field, value)

    def tickPrice(self, tickerId, field, price, attribs):
        ladderex.tick_signal.emit(tickerId, field, price)

    def contractDetails(self, reqId: int, contractDetails: ContractDetails):
        # pass first contract only to ct_details for each ct_counter
        if ladderex.ct_details is None and reqId == ladderex.ct_counter:
            ladderex.ct_details = contractDetails.contract

    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice, permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
        if not orderId in tws_Trade.tml: return # order ids will come in from existing trades
        ladderex.order_signal.emit(orderId, status, int(filled), avgFillPrice)

    def openOrder(self, orderId, contract, order, orderState):
        if not orderId in tws_Trade.tml: return
        L = ladderex
        t = tws_Trade.tml[orderId]

        if t.size != order.totalQuantity:
            ladderex.order_change_signal.emit(orderId, 'size',  int(order.totalQuantity), order)
        
        if t.is_stop and t.price != order.auxPrice:
            ladderex.order_change_signal.emit(orderId, 'price', order.auxPrice, order)

        if not t.is_stop and t.price != order.lmtPrice and t.order.orderType != 'MKT':
            ladderex.order_change_signal.emit(orderId, 'price', order.lmtPrice, order)

    def openOrderEnd(self):
        self.open_order_req_ended = True

    def error(self, reqId, errorCode, errorString, msg, _ignore):
        if reqId in tws_Trade.tml:
            ladderex.order_signal.emit(reqId, msg, -1, 0.0)
                
        if errorString == 200 or errorString == 321:
            if reqId == ladderex.ct_counter:
                ladderex.ct_details = 'failed'

    def position(self, account, contract, pos, avgCost):
        avg_str = str(round(avgCost, 2))
        if contract.secType == 'OPT':
            avg_str = str(round(avgCost / 100, 2))
        pos_str = str(int(pos))

        ladderex.pos_signal.emit(contract.conId, pos_str, avg_str)

class twsapiClient(EClient):
    def __init__(self, wrapper):
        EClient.__init__(self, wrapper)

class twsapiApp(twsapiWrapper, twsapiClient):
  def __init__(self):
    twsapiWrapper.__init__(self)
    twsapiClient.__init__(self, wrapper=self)

# GLOBALS
ibapp = twsapiApp()
qapp  = QApplication(sys.argv)

month_array      = ['', 'Jan', 'Feb', 'March', 'April', 'May', 'June', 'July', 'Aug', 'Sept', 'Oct', 'Nov', 'Dec']
month_array_cond = ['', 'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUNE', 'JULY', 'AUG', 'SEPT', 'OCT', 'NOV', 'DEC']
min_sign = '\uff0d' ; max_sign = '\uff0b'

ibpos_dict = { }

# handle ctr + c
def signal_handler(sig, frame):
    print('^C.. ', end='')
    qapp.quit()
signal.signal(signal.SIGINT, signal_handler)

# qthread that runs the tws api or tcp socket 
class basicWorker(QThread):
    name = 'none'
    
    def run(p):

        if p.name == 'ibworker':
            ibapp.run()

        if p.name == 'socketworker':
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(('localhost', 3002))
            sock.listen()

            while True:
                (client_socket, _) = sock.accept()
                data     = client_socket.recv(1024)
                data_utf = data.decode('utf8')
                
                ladderex.sockmsg_signal.emit(data_utf, 'load_inst')

                # NOTE: assumes one chunk ... very lazy implementation

# floating panels for various tasks
class floating_panel:

    def prepare(self, *args):
        P = self
        L = ladderex 
        
        if P.name == 'order_diag':
            pm = P.graphics['back'][0]
            qp = QPainter()
            pm.fill(QColor(39, 40, 34))
            qp.begin(pm)

            font   = QFont("Georgia", 11)
            font_b = QFont("Consolas", 10, QFont.Weight.DemiBold)
            font.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)
            font_b.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)

            # print last 20 errors and time received
            y = 4 - P.offset
            for e in L.target.tws_errors[:20]:
                date_str  = e[0]
                error_str = e[1]
            
                qp.fillRect(0, y, P.w, 16, QColor(10, 10, 10))
                qp.setFont(font_b)
                qp.setPen(QColor(141, 206, 6))
                qp.drawText(2, y + 12, date_str)
                
                qp.setFont(font)
                qp.setPen(QColor(202,202,202))
                br = qp.drawText(QRectF(4, y + 17, P.w - 8, P.h * 4), Qt.TextFlag.TextWordWrap, error_str)

                y += int(br.height()) + 21
            
            qp.fillRect(0, 0, P.w, 3, QColor(238, 220, 130))
            qp.fillRect(0, P.h - 3, P.w, 3, QColor(238, 220, 130))

            qp.end()
        
        if P.name == 'enter_inst':
            pm = P.graphics['back'][0]
            pm.fill(QColor(40, 40, 40, 255))
            
            f = QFont("Consolas", 14)
            f_small = QFont("Consolas", 10)
            f.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)
            f_small.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)

            qp = QPainter()
            qp.begin(pm)
                
            qp.setPen(QColor(240, 240, 240))
            if len(P.inst_str) == 21 or '/' in P.inst_str:
                qp.setPen(QColor(228, 218, 110))
            if P.error_flag:
                qp.setPen(QColor(188,102,109))
                P.error_flag = False
            
            disp_str = P.inst_str.replace(' ','-')
            qp.setFont(f)
            if len(disp_str) > 14: qp.setFont(f_small)

            br = qp.boundingRect(L.blank_r, 0, disp_str)
            qp.drawText(QPoint(4, 16), disp_str)

            qp.setPen(QColor(140, 140, 140))
            qp.drawText(QPoint(6 + br.width(), 16), '_')
                    
            qp.end()

        if P.name == 'toolbox':
            pm = P.graphics['back'][0]
            pm.fill(QColor(224,224,224))
            
            qp = QPainter()
            qp.begin(pm)

            button_style = QPen(QColor(40,40,40), 4)
            
            font_b = QFont("Consolas", 12, QFont.Weight.DemiBold)
            font_b.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)

            qp.fillRect(0, 0, P.w, P.h, QBrush(QColor(20,20,20), Qt.BrushStyle.BDiagPattern))
            qp.setFont(font_b)

            for i, t in enumerate(P.tools):
                qp.setBrush(QColor(200, 200, 200))
                qp.setPen(button_style)
                y_off = i * (P.row_h + 8) + 8
                qp.drawRect(8, y_off, P.w - 16, P.row_h)

                center = int(P.row_h / 2 + 6)
                tool_str = t.upper().replace('_', ' ')
                
                if '[ON]'  in tool_str: # toggles
                    tool_str = tool_str.replace('[ON]', '')
                    qp.fillRect(P.w - 8 - 28, y_off + 2, 26, P.row_h - 4, QColor(229,126,0))
                    qp.drawText(P.w - 16 - 16, y_off + center, 'ON')
                
                qp.drawText(8 + 4, y_off + center, tool_str)
                    
                P.colrects[t] = [ P.x + 8, P.y + y_off, P.w - 16, P.row_h ] 

            qp.end()
        
        if P.name == 'opt_switcher':
            pm = P.graphics['back'][0]
            pm.fill(QColor(234,234,234))
            # reset colrects on scroll etc
            P.colrects = { 'back': P.colrects['back'], 'edit' : P.colrects['edit'] }
            
            T = L.target
            if T.ct_type == 'opt':
                T = T.parent
            tl = T.opt_list

            qp = QPainter()
            qp.begin(pm)
            
            panel_border = QPen(QColor(20,20,20), 4)

            font   = QFont("Helvetica", 10, QFont.Weight.DemiBold)
            font_b = QFont("Consolas", 11, QFont.Weight.DemiBold)
            font.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)
            font_b.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)

            qp.setFont(font)
            br_ = qp.boundingRect(L.blank_r, 0, '\'99')

            if P.offset < 0: 
                P.offset = 0

            y = 3 - P.offset
            y_off = 0
            
            for i, o in enumerate(tl):
                qp.setPen(QColor(0, 0, 0))
                
                y_off = y + br_.height() + 4
                    
                n = o.ct_str[6:] 
                pname = o.ct_str[:6]
                pname = pname.replace(' ', '')

                strike = int(n[7:]) / 1000
                name = pname             
                opt_dir = n[6:7]
                exp_str = n[:2] + ' ' + month_array_cond[int(n[2:4])] + ' ' + n[4:6]
                    
                qp.setPen(QColor(0, 0, 0))
                qp.setFont(font)

                if P.edit_mode and P.edit_focus == o.ct_str:
                    qp.fillRect(0,y + 4,8,P.row_h - 8, QColor(50,205,50)) 
                elif opt_dir == 'C':
                    qp.fillRect(0,y + 4,8,P.row_h - 8, QColor(100,104,185))
                else:
                    qp.fillRect(0,y + 4,8,P.row_h - 8, QColor(191,48,0))
                
                x_off = 12
                
                qp.drawText(x_off, y_off, exp_str)
                br = qp.boundingRect(L.blank_r, 0, 'OO JUNE OO')
                x_off += br.width() + 4
                qp.drawText(x_off, y_off, '{0:g}'.format(strike) + ' ' + opt_dir)
                
                if o.starred:
                    qp.drawText(P.w - 12, y_off - 6, '*')

                top_y = P.y + y
                bot_y = P.y + y + P.row_h

                if bot_y <= P.y or top_y >= P.y + P.h: # fully out of bounds
                    pass
                elif top_y >= P.y and bot_y <= P.y + P.h: # fully in bounds
                    P.colrects[o.ct_str] = [ P.x, top_y, P.w, P.row_h ] 
                else:
                    if top_y < P.y: # top above bounds, else bottom below bounds
                        dif = P.y - top_y
                        P.colrects[o.ct_str] = [ P.x, P.y, P.w, P.row_h - dif ] 
                    else:
                        P.colrects[o.ct_str] = [ P.x, top_y, P.w, P.y + P.h - top_y ] 

                y += P.row_h
                
                qp.setPen(QColor(0,0,0))
                qp.drawLine(0, y - 1, P.w, y)
                
                if y > P.y + P.h: break
            
            qp.setBrush(Qt.BrushStyle.NoBrush)
            qp.setPen(panel_border)
            qp.drawRect(1,1,P.w - 2,P.h - 2)
                
            qp.end()
            
            pm = P.graphics['edit'][0]
            pm.fill(QColor(216,192,216))
            if P.edit_mode:
                pm.fill(QColor(50,205,50)) 
            
            panel_border = QPen(QColor(28,52,100), 4)

            qp.begin(pm)
            qp.setFont(font_b)
            qp.setBrush(Qt.BrushStyle.NoBrush)
            qp.setPen(panel_border)
            qp.drawRect(1,1,P.edit_box_h - 2,P.edit_box_h - 2)
            qp.setPen(QColor(0,0,0))
            qp.drawText(6, 16, 'E')
            qp.end()

        if P.name == 'oca_group_create':
            for r in P.trades.copy():
                if r not in r.inst.trades:
                    P.trades.remove(r)
            
            alpha = 0 if P.min_max == max_sign else 255

            pm = P.graphics['back'][0]
            pm.fill(QColor(224,244,224,alpha))
            
            qp = QPainter()
            qp.begin(pm) # paint bg which can be clicked to rerand name
            qp.setPen(QColor(0,0,0,alpha))
            qp.setFont(QFont('Consolas', 12, QFont.Weight.DemiBold))
            qp.drawText(6, 16, P.group_name)
            qp.setFont(QFont('Consolas', 14))
            qp.drawText(6, 38, '\u27f3')
            qp.end()
            
            pm = P.graphics['close'][0]
            pm.fill(QColor(216,192,216))
            
            qp.begin(pm) # paint exit button
            panel_border = QPen(QColor(20,20,20), 4)
            qp.setPen(panel_border)
            qp.drawRect(1,1,pm.width() - 2, pm.height() - 2)
            qp.setPen(QColor(0,0,0))
            qp.setFont(QFont('Consolas', 12, QFont.Weight.DemiBold))
            br = qp.boundingRect(L.blank_r, 0, 'ESC')
            qp.drawText(pm.width() // 2 - br.width() // 2 - 1, 17, 'ESC')
            qp.end()
            
            pm = P.graphics['type_sw'][0]
            pm.fill(QColor(216,192,216,alpha))
            
            qp.begin(pm) # paint oca type switcher
            panel_border = QPen(QColor(20,20,20,alpha), 4)
            qp.setPen(panel_border)
            qp.drawRect(1,1,pm.width() - 2, pm.height() - 2)

            qp.setPen(QColor(0,0,0,alpha))
            qp.setFont(QFont('Consolas', 12, QFont.Weight.DemiBold))
            
            qp.drawText(4, 15, 'oca type:')
            type_str = 'cancel'
            if P.oca_type == 2:
                type_str = 'red w/ blk'
            if P.oca_type == 3:
                type_str = 'reduce'
            
            qp.drawText(4, 30, type_str)

            qp.end()
            
            pm = P.graphics['submit'][0]
            if len(P.trades) > 1:
                pm.fill(QColor(216,192,216,alpha))
            else:
                pm.fill(QColor(224,244,224,alpha))
            
            qp.begin(pm) # paint submit
            panel_border = QPen(QColor(20,20,20,alpha), 4)
            qp.setPen(panel_border)
            qp.drawRect(1,1,pm.width() - 2, pm.height() - 2)
            
            os = pm.width() - 1 
            qp.fillRect(os - 10, 16,      4,  16, QColor(40,40,40,alpha))
            qp.fillRect(os - 30, 16 + 12, 20, 4,  QColor(40,40,40,alpha))
            qp.fillRect(os - 30, 16 + 10, 6,  8,  QColor(40,40,40,alpha))
            qp.fillRect(os - 34, 16 + 11, 2,  6,  QColor(40,40,40,alpha))
            qp.end()
            
            pm = P.graphics['min_max'][0]
            pm.fill(QColor(160,160,160))
            
            qp = QPainter()
            qp.begin(pm)
            
            qp.setPen(QPen(QColor(20,20,20), 4))

            qp.drawRect(1,1,pm.width() - 2, pm.height() - 2)
            
            qp.setPen(QColor(0,0,0))
            qp.setFont(QFont('Consolas', 12, QFont.Weight.DemiBold))
            br = qp.boundingRect(L.blank_r, 0, P.min_max)
            qp.drawText(pm.width() // 2 - br.width() // 2 - 1, 16, P.min_max)

            qp.end()
        
        if P.name == 'price_cond_create':
            alpha = 0 if P.min_max == max_sign else 255

            pm = P.graphics['back'][0]
            pm.fill(QColor(224,244,224, alpha))
            
            qp = QPainter()
            qp.begin(pm)
            
            qp.setFont(QFont("Consolas", 12, QFont.Weight.DemiBold))
            qp.setPen(QPen(QColor(20,20,20,alpha), 3))

            # check if any of these spc trades have been deleted since last display
            if P.trigger is not None:
                if P.trigger not in P.trigger.inst.trades: P.trigger = None
            
            for r in P.target.copy():
                if r not in r.inst.trades:
                    P.target.remove(r)

            sign = ' >= ' if P.is_more else ' <= '
            if P.trigger is not None:
                qp.drawText(4, 16, P.trigger.inst.name)
                br = qp.boundingRect(L.blank_r, 0, ' >= ')
                qp.fillRect(4, 20, br.width(), br.height(), QColor(216,192,216,alpha))
                qp.drawRect(4, 20, br.width(), br.height())
                qp.drawText(4, 34, sign)
                qp.drawText(4 + br.width() + 6, 34, str(P.trigger.price))
                    
                P.colrects['more_sw'] = [ P.x + 2, P.y + 20, br.width(), br.height() ] 
                
            os = P.w - 4
            qp.fillRect(P.w - 46, 4, 42, 42, QColor(224,244,224,alpha))
            if P.trigger != None and len(P.target):
                qp.fillRect(P.w - 46, 4, 42, 42, QColor(216,192,216,alpha))
                P.colrects['submit'] = [ P.x + P.w - 46, P.y + 4, 42, 42 ] 
            qp.drawRect(P.w - 46, 4, 42, 42)
            
            qp.fillRect(os - 10, 20,      4,  16, QColor(40,40,40,alpha))
            qp.fillRect(os - 30, 20 + 12, 20, 4,  QColor(40,40,40,alpha))
            qp.fillRect(os - 30, 20 + 10, 6,  8,  QColor(40,40,40,alpha))
            qp.fillRect(os - 34, 20 + 11, 2,  6,  QColor(40,40,40,alpha))

            qp.end()
            
            panel_border = QPen(QColor(20,20,20), 4)
            
            pm = P.graphics['min_max'][0]
            pm.fill(QColor(160,160,160))
            
            qp = QPainter()
            qp.begin(pm)
            
            qp.setPen(panel_border)

            qp.drawRect(1,1,pm.width() - 2, pm.height() - 2)
            
            qp.setPen(QColor(0,0,0))
            qp.setFont(QFont('Consolas', 12, QFont.Weight.DemiBold))
            br = qp.boundingRect(L.blank_r, 0, P.min_max)
            qp.drawText(pm.width() // 2 - br.width() // 2 - 1, 16, P.min_max)

            qp.end()
                
            pm = P.graphics['close'][0]
            pm.fill(QColor(216,192,216))
            
            qp = QPainter()
            qp.begin(pm)
            
            qp.setPen(panel_border)

            qp.drawRect(1,1,pm.width() - 2, pm.height() - 2)
            
            qp.setPen(QColor(0,0,0))
            qp.setFont(QFont('Consolas', 12, QFont.Weight.DemiBold))
            br = qp.boundingRect(L.blank_r, 0, 'ESC')
            qp.drawText(pm.width() // 2 - br.width() // 2 - 1, 17, 'ESC')

            qp.end()
            
            pm = P.graphics['mkt_sw'][0]
            pm.fill(QColor(216,192,216))

            if P.is_mkt:
                pm.fill(QColor(229,126,0))
            
            qp = QPainter()
            qp.begin(pm)
            
            qp.setPen(panel_border)

            qp.drawRect(1,1,pm.width() - 2, pm.height() - 2)

            qp.setPen(QColor(0,0,0))
            qp.setFont(QFont('Consolas', 12, QFont.Weight.DemiBold))
            if P.is_mkt:
                br = qp.boundingRect(L.blank_r, 0, 'MKT')
                qp.drawText(pm.width() // 2 - br.width() // 2 - 1, 17, 'MKT')
            else:
                br = qp.boundingRect(L.blank_r, 0, 'LMT')
                qp.drawText(pm.width() // 2 - br.width() // 2 - 1, 17, 'LMT')

            qp.end()
        
    def collision(self, n, E, enum = None, key_char = '\0', mod = None):
        P = self
        T = ladderex.target
        L = ladderex
        
        if P.name == 'order_diag':
            scroll_dist = 28
            if 'key' in E:
                if enum == Qt.Key.Key_H or enum == Qt.Key.Key_Return or enum == Qt.Key.Key_Escape:
                    E = 'click'
                elif enum == Qt.Key.Key_J or enum == Qt.Key.Key_Down:
                    E = 'scroll_down' ; scroll_dist = 28 * 2
                elif enum == Qt.Key.Key_K or enum == Qt.Key.Key_Up:
                    E = 'scroll_up'   ; scroll_dist = 28 * 2
                elif enum == Qt.Key.Key_G or enum == Qt.Key.Key_Home:
                    P.offset = 0
                    P.prepare() ; L.update()

            if E == 'click':
                L.activated_floating_panel = None
                L.update() ; return
            if 'scroll' in E:
                if 'up' in E:
                    P.offset -= scroll_dist
                else:
                    P.offset += scroll_dist
                if P.offset < 0: P.offset = 0
                P.prepare() ; L.update() ; return

        if P.name == 'enter_inst':
            hist = L.target_hist[:-1] 
            if L.target is not None and L.target.ct_type == 'opt':
                hist = L.target_hist

            c = key_char.upper()
            if 'key' in E:
                if enum == Qt.Key.Key_Escape:
                    n = 'out_of_bounds'
                elif enum == Qt.Key.Key_Backspace:
                    P.inst_str = P.inst_str[:-1]
                elif enum == Qt.Key.Key_Delete:
                    P.inst_str = ''
                elif enum == Qt.Key.Key_Return:
                    s = tws_Instrument(P.inst_str)
                    s = s.setup()
                    if s: 
                        s.make_target()
                        n = 'out_of_bounds'
                    else:
                        P.error_flag = True

                elif enum == Qt.Key.Key_Up and len(hist):
                    P.histi -= 1
                    try: hist[P.histi] 
                    except: P.histi += 1
                    P.inst_str = hist[P.histi]
                elif enum == Qt.Key.Key_Down and len(hist):
                    if P.histi == -1: 
                        P.histi = -1
                    elif P.histi == 0: 
                        P.histi = 0
                    else:
                        P.histi += 1
                        P.inst_str = hist[P.histi]
                elif mod == Qt.KeyboardModifier.ControlModifier and enum == Qt.Key.Key_V:
                    E = 'click' ; enum = Qt.MouseButton.MiddleButton
                elif not len(c):
                    pass
                elif ord(c) >= ord('A') and ord(c) <= ord('Z'):
                    P.inst_str += c 
                elif ord(c) >= ord('0') and ord(c) <= ord('9'):
                    P.inst_str += c 
                elif ord(c) == ord('/'):
                    P.inst_str += '/'
                elif c == ' ' or c == '.': # NOTE: tws api wants BRK.B as BRK B
                    P.inst_str += ' '
            
            if 'click' in E and enum == Qt.MouseButton.MiddleButton:
                whitelist = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ/ 0123456789')
                txt = qapp.clipboard().text().upper()
                txt = ''.join(filter(whitelist.__contains__, txt))
                if len(txt) <= 21: P.inst_str = txt
                self.prepare() ; L.update() ; return

            # close the display
            if n == 'out_of_bounds':
                L.activated_floating_panel = None
                L.update() ; return
                    
            P.prepare() ; L.update()

        if P.name == 'toolbox':
            T = L.target
            if 'key' in E:
                if enum == Qt.Key.Key_Escape or enum == Qt.Key.Key_T: 
                    n = 'out_of_bounds'
            
            # close the display
            if n == 'out_of_bounds':
                L.activated_floating_panel = None
                L.update() ; return
            
            if E == 'click':
                if n == 'back':
                    return

                if   n == 'enter_inst':
                    L.activated_floating_panel = floating_panel('enter_inst')
                    L.activated_floating_panel.prepare() ; L.update() ; return
                
                if 'order_overlap' in n:
                    if L.order_overlap == False:
                        P.tools[4] = 'order_overlap[on]' ;L.order_overlap=True
                    else:
                        P.tools[4] = 'order_overlap'     ;L.order_overlap=False
                    P.prepare() ; L.update() ; return

                if T is None or T.mpl_offset is None: return
            
                if n == 'order_diag':
                    L.activated_floating_panel = floating_panel('order_diag')
                    L.activated_floating_panel.prepare()
                elif n == 'oca_group_create':
                    if L.activated_floating_panel_ext: L.activated_floating_panel_ext.collision('close', 'click', None)
                    L.activated_floating_panel_ext = floating_panel('oca_group_create')
                    L.activated_floating_panel = None
                elif n == 'price_cond_create':
                    if L.activated_floating_panel_ext: L.activated_floating_panel_ext.collision('close', 'click', None)
                    L.activated_floating_panel_ext = floating_panel('price_cond_create')
                    L.activated_floating_panel_ext.prepare()
                    L.activated_floating_panel = None

                L.update() ; return
        
        if P.name == 'opt_switcher':
            T = L.target
            if 'key' in E:
                if enum == Qt.Key.Key_Escape:
                    n = 'out_of_bounds'
                elif enum == Qt.Key.Key_O or enum == Qt.Key.Key_S.value: 
                    n = 'out_of_bounds'
                elif enum == Qt.Key.Key_E.value: 
                    P.edit_mode ^= True
                    P.edit_focus = None
                    P.prepare() ; L.update() ; return
                    
                ls = []
                if T.ct_type == 'opt': 
                    ls = T.parent.opt_list
                else:
                    ls = T.opt_list
                
                # change display order of options
                if P.edit_mode and P.edit_focus is not None:
                    
                    o_targ = None
                    o_index = -1
                    for o in ls:
                        if o.ct_str == P.edit_focus: o_targ = o

                    o_index = ls.index(o_targ)

                    if enum == Qt.Key.Key_Up:
                        if o_index > 0:
                            ls[o_index] = ls[o_index - 1]
                            ls[o_index - 1] = o_targ
                            P.prepare() ; L.update() ; return
                    if enum == Qt.Key.Key_Down:
                        if o_index < len(ls) - 1:
                            ls[o_index] = ls[o_index + 1]
                            ls[o_index + 1] = o_targ
                            P.prepare() ; L.update() ; return

                if P.offset == 0: # quick select with number keys 1-5
                    l = list(P.colrects)
                    if enum == Qt.Key.Key_1 and len(l) > 2:
                        n = l[2] ; E = 'click' ; enum = Qt.MouseButton.LeftButton
                    elif enum == Qt.Key.Key_2 and len(l) > 3:
                        n = l[3] ; E = 'click' ; enum = Qt.MouseButton.LeftButton
                    elif enum == Qt.Key.Key_3 and len(l) > 4:
                        n = l[4] ; E = 'click' ; enum = Qt.MouseButton.LeftButton
                    elif enum == Qt.Key.Key_4 and len(l) > 5:
                        n = l[5] ; E = 'click' ; enum = Qt.MouseButton.LeftButton
                    elif enum == Qt.Key.Key_5 and len(l) > 6:
                        n = l[6] ; E = 'click' ; enum = Qt.MouseButton.LeftButton

            # close the display
            if n == 'out_of_bounds':
                L.activated_floating_panel = None
                L.update() ; return

            if 'scroll' in E and n != 'out_of_bounds':
                if 'up' in E:
                    P.offset -= 18 # px
                else:
                    P.offset += 18
                P.prepare()
                L.update() ; return
            
            if E == 'click':
                if n == 'back':
                    return
                if n == 'edit':
                    P.edit_mode ^= True
                    P.edit_focus = None
                    P.prepare() ; L.update() ; return
                if P.edit_mode and enum == Qt.MouseButton.LeftButton:
                    P.edit_focus = n
                    P.prepare() ; L.update() ; return
                if enum == Qt.MouseButton.RightButton:
                    for s in L.iml.values():
                        if s.ct_str == n:
                            s.starred ^= True
                            P.prepare() ; L.update() ; return
                if enum == Qt.MouseButton.LeftButton:
                    s = tws_Instrument(n, False)
                    s = s.setup()
                    if s:
                        L.activated_floating_panel = None
                        s.make_target()
                    return

        if P.name == 'oca_group_create':
            ret = None
            
            if 'key' in E:
                if enum == Qt.Key.Key_Escape:
                    E = 'click' ; n = 'close'
                elif enum == Qt.Key.Key_C:
                    E = 'click' ; n = 'close'
                elif enum == Qt.Key.Key_V: # handle other extended panels 
                    pass
                elif enum == Qt.Key.Key_Return:
                    E = 'click' ; n = 'submit'
                else:
                    return enum # keyPress will then pass this key on to ladder

            if E == 'click':
                if n == 'close':
                    for r in P.trades:
                        r.inst.trades.remove(r)

                    L.activated_floating_panel_ext = None ; L.update() ; return ret

                if n == 'min_max':
                    if P.min_max == min_sign:
                        P.min_max = max_sign 
                    else:
                        P.min_max = min_sign
                    P.prepare() ; L.update() ; return

                if P.min_max == max_sign: # main display is minimized, send click to ladder
                    return enum

                if n == 'back': # refresh group name
                    P.group_name = ''
                    for i in range(4): P.group_name += chr(random.randrange(65, 65 + 25))
                    P.prepare() ; L.update() ; return

                if n == 'type_sw':
                    P.oca_type += 1
                    if P.oca_type > 3: P.oca_type = 1
                    P.prepare() ; L.update() ; return

                if n == 'submit':
                    if len(P.trades) < 2: return

                    for t in P.trades: # grab trades that have not been deleted
                        t.order.ocaGroup = P.group_name
                        t.order.ocaType  = P.oca_type
                        t.spc_descriptor = 'oca_group' 
                        t.status = 'init'
                        t.spc_icon = None

                    for t in P.trades:
                        t.inst.trades.remove(t) # placed trade will be added when 'ok' 
                        ibapp.placeOrder(t.id, t.inst.ct, t.order)
                        L.click_indicator.append([t, time.time(), 'submit'])

                    L.activated_floating_panel_ext = None ; L.update() ; return
        
        if P.name == 'price_cond_create':
            ret = None

            if 'key' in E:
                if enum == Qt.Key.Key_Escape:
                    E = 'click' ; n = 'close'
                elif enum == Qt.Key.Key_V:
                    E = 'click' ; n = 'close'
                elif enum == Qt.Key.Key_C: # handle switch to other extended panels 
                    pass
                    #E = 'click' ; n = 'close' ; ret = enum
                elif enum == Qt.Key.Key_Return:
                    E = 'click' ; n = 'submit'
                else:
                    return enum # keyPress will then pass this key on to ladder

            if E == 'click':
                if n == 'mkt_sw':
                    P.is_mkt ^= True
                    P.prepare() ; L.update() ; return
            
                if n == 'min_max':
                    if P.min_max == min_sign:
                        P.min_max = max_sign 
                    else:
                        P.min_max = min_sign
                    P.prepare() ; L.update() ; return
            
                if n == 'close':
                    if P.trigger is not None:
                        P.trigger not in (l:=P.trigger.inst.trades) or l.remove(P.trigger)
                    for r in P.target:
                        r not in (l:=r.inst.trades) or l.remove(r)
                    
                    L.activated_floating_panel_ext = None ; L.update() ; return ret

                if P.min_max == max_sign: # main display is minimized, send click to ladder
                    return enum
                
                if n == 'more_sw':
                    P.is_more ^= True
                    P.prepare() ; L.update() ; return

                if n == 'submit':
                    if P.trigger is None: return
                    if not len(P.target): return

                    for r in P.target:
                        trigger = P.trigger
                        cond = PriceCondition(PriceCondition.TriggerMethodEnum.Default, trigger.inst.ct.conId, 'SMART', P.is_more, trigger.price)

                        trade = r 
                        order = trade.order

                        if P.is_mkt:
                            order.orderType = 'MKT' ; order.outsideRth = False
                        
                        order.conditions.append(cond)

                        trade.inst.trades.remove(trade) # placed trade will be added when 'ok' 
                        ibapp.placeOrder(trade.id, trade.inst.ct, trade.order)

                        trade.spc_descriptor = 'price_triggered' 
                        trade.status = 'init'
                        trade.spc_icon = None
                        
                        L.click_indicator.append([trigger, time.time(), 'submit'])
                        L.click_indicator.append([trade, time.time(), 'submit'])

                    P.trigger.inst.trades.remove(P.trigger)

                    L.activated_floating_panel_ext = None ; L.update() ; return
            
    def pass_trade(self, trade):
        P = self
        L = ladderex
                
        if P.name == 'oca_group_create':
            trade.spc_icon = L.spc_icon_group
            trade.status   = 'spc'
            P.trades.append(trade)
        
        if P.name == 'price_cond_create':
            found_home = False

            if P.trigger is not None:
                if not P.trigger in P.trigger.inst.trades:
                    P.trigger = trade ; found_home = True
            else:
                P.trigger = trade ; found_home = True

            if not found_home: 
                trade.spc_icon = L.spc_icon_target
                trade.status   = 'spc'
                P.target.append(trade)

            if P.trigger is not None: 
                P.trigger.spc_icon = L.spc_icon_trigger ; P.trigger.status = 'spc'
                if P.trigger.trade_type == 'B':
                    P.is_more = True
                else:
                    P.is_more = False
            
        P.prepare() ; L.update()

    def __init__(self, panel_name, x=0, y=0, w=6, h=6):
        P = self
        L = ladderex

        # init vars
        P.name = panel_name 
        P.ignore_oob = False

        P.x = x ; P.y = y ; P.w = w ; P.h = h

        P.colrects  = { } # name : [x, y, w, h]
        P.graphics  = { } # name : [pm, x, y]

        # setup panel based on name
        if P.name == 'enter_inst':
            P.h = 52
            P.x = int(L.ladder_width / 2 - L.fp_width / 2)
            P.y = L.ladder_ctrl_height + int(L.ladder_height / 2 - P.h / 2) 
            P.w = L.fp_width

            pm = QPixmap(QSize(P.w, P.h))

            P.inst_str = ''
            P.histi = 0
            P.error_flag = False
            
            P.colrects['back'] = [ P.x, P.y, P.w, P.h ] 
            P.graphics['back'] = [ pm, P.x, P.y ]
        
        if P.name == 'order_diag': # used to check for order errors, but will tws send them? ;)
            P.w = L.ladder_win_width
            P.h = L.ladder_height - L.ladder_row_spacing * 2
            P.x = 0
            P.y = L.ladder_ctrl_height + L.ladder_row_spacing
            pm = QPixmap(QSize(P.w, P.h))

            P.colrects['back'] = [ P.x, P.y, P.w, P.h ] 
            P.graphics['back'] = [ pm, P.x, P.y ]

            P.offset = 0
        
        if P.name == 'opt_switcher':
            P.rows = 5
            P.row_h = 30
            P.h = P.row_h * P.rows + int(P.row_h * 0.5)
            P.x = int(L.ladder_width / 2 - L.fp_width / 2)
            P.w = L.fp_width
            
            if P.y == -1: # set y to center
                P.y = L.ladder_ctrl_height + int(L.ladder_height / 2 - P.h / 2)
            else:
                P.y = L.ladder_ctrl_height + 4

            pm = QPixmap(QSize(P.w, P.h))

            P.offset     = 0
            P.edit_mode  = False
            P.edit_focus = None

            P.colrects['back'] = [ P.x, P.y, P.w, P.h ] 
            P.graphics['back'] = [ pm, P.x, P.y ]

            ed_h = 22
            P.edit_box_h = ed_h
            pm = QPixmap(QSize(ed_h, ed_h))
            P.graphics['edit'] = [ pm, L.ladder_win_width - int(ed_h * 1.5), L.ladder_win_height - int(ed_h * 1.5) ]
            P.colrects['edit'] = [ L.ladder_win_width - int(ed_h * 1.5), L.ladder_win_height - int(ed_h * 1.5), ed_h, ed_h ] 
        
        if P.name == 'toolbox':
            P.tools = [ 'enter_inst', 'oca_group_create', 'price_cond_create', 'order_diag', 'order_overlap' ]
            if L.order_overlap == True: 
                P.tools[4] = 'order_overlap[on]'

            P.x = int(L.ladder_width / 2 - L.fp_width / 2)
            P.w = L.fp_width

            P.row_h = 30
            full_row_h = P.row_h + 8

            P.h = full_row_h * len(P.tools) + 8

            if P.y == -1: # center panel on screen
                P.y = L.ladder_ctrl_height + int(L.ladder_height / 2 - P.h / 2)
            else:
                P.y = L.ladder_ctrl_height + 4

            pm = QPixmap(QSize(P.w, P.h))

            P.colrects['back'] = [ P.x, P.y, P.w, P.h ] 
            P.graphics['back'] = [ pm, P.x, P.y ]
        
        if P.name == 'oca_group_create': 
            P.h = L.ladder_row_spacing * 3 + 2
            P.x = 0
            P.y = L.ladder_win_height - L.ladder_bot_pane_h - P.h - L.ladder_row_spacing
            P.w = L.ladder_win_width

            pm = QPixmap(QSize(P.w, P.h))

            P.colrects['back'] = [ P.x, P.y, P.w, P.h ] 
            P.graphics['back'] = [ pm, P.x, P.y ]
            
            pm = QPixmap(QSize(100, 42))
            P.colrects['type_sw'] = [ P.w - 42 - 8 - 100, P.y + 4, 100, 42 ] 
            P.graphics['type_sw'] = [ pm, P.w - 42 - 8 - 100, P.y + 4 ]
            
            pm = QPixmap(QSize(54, 24))
            P.colrects['close'] = [ L.ladder_win_width - 56, L.ladder_ctrl_height + 2, 54, 24 ] 
            P.graphics['close'] = [ pm, L.ladder_win_width - 56, L.ladder_ctrl_height + 2 ]

            pm = QPixmap(QSize(42, 42))
            P.colrects['submit'] = [ P.w - 42 - 4, P.y + 4, 42, 42 ] 
            P.graphics['submit'] = [ pm, P.w - 42 - 4, P.y + 4 ]
            
            w = L.fill_pane_width + 6 ; h = 20
            pm = QPixmap(QSize(w, h))
            P.colrects['min_max'] = [ L.ladder_win_width - w - 2, P.y - int(h * 1.5), w, h ] 
            P.graphics['min_max'] = [ pm, L.ladder_win_width - w - 2, P.y - int(h * 1.5) ]

            P.min_max = min_sign # hide main floating display
            P.group_name = '' 
            P.oca_type   = 1
            P.trades     = [ ]
            for i in range(4): P.group_name += chr(random.randrange(65, 65 + 25))

            P.prepare()
        
        if P.name == 'price_cond_create': 
            P.h = L.ladder_row_spacing * 3 + 2
            P.x = 0 ; P.y = L.ladder_win_height - L.ladder_bot_pane_h - P.h - L.ladder_row_spacing
            P.w = L.ladder_win_width

            pm = QPixmap(QSize(P.w, P.h))

            P.colrects['back'] = [ P.x, P.y, P.w, P.h ] 
            P.graphics['back'] = [ pm, P.x, P.y ]
            
            pm = QPixmap(QSize(54, 24))
            P.colrects['close'] = [ L.ladder_win_width - 56, L.ladder_ctrl_height + 2, 54, 24 ] 
            P.graphics['close'] = [ pm, L.ladder_win_width - 56, L.ladder_ctrl_height + 2 ]
            
            pm = QPixmap(QSize(54, 24))
            P.colrects['mkt_sw'] = [ L.ladder_win_width - 56, L.ladder_ctrl_height + 4 + 24, 54, 24 ] 
            P.graphics['mkt_sw'] = [ pm, L.ladder_win_width - 56, L.ladder_ctrl_height + 4 + 24 ]
            
            w = L.fill_pane_width + 6 ; h = 20
            pm = QPixmap(QSize(w, h))
            P.colrects['min_max'] = [ L.ladder_win_width - w - 2, P.y - int(h * 1.5), w, h ] 
            P.graphics['min_max'] = [ pm, L.ladder_win_width - w - 2, P.y - int(h * 1.5) ]

            P.trigger = None
            P.target  = [ ] # can have several targets
            P.is_more = True
            P.is_mkt  = False
            P.min_max = min_sign

# qthread that waits for a valid bid & ask and then sets mpl_offset
# if does not get bid & ask in time will use just bid/ask or last or close
# with mpl_offset not None the ladder will be drawn
class ladderWorker(QThread):
    target = None

    def run(p):
        L = ladderex
        t = p.target
            
        delay = 100
        start_time = time.time()

        while True: 
            if t.ask > 0 and t.bid > 0:
                mid = t.ask ; t.init_ask = t.ask
                t.current_zoom_inc = t.set_zoom_inc(mid)
                
                t.snap_offset_mid()
                break
            
            # after 5 seconds, switch to a 1 second delay in background
            if L.target is not t and delay == 100 and time.time() - start_time > 10:
                delay = 10**6

            if L.target is t and time.time() - start_time > 1:
                snap_one = None 
                if   t.ask   > 0: snap_one = t.ask 
                elif t.bid   > 0: snap_one = t.bid
                elif t.last  > 0: snap_one = t.last
                elif t.close > 0: snap_one = t.close

                if snap_one is not None:
                    mid = snap_one ; t.init_ask = snap_one
                    t.current_zoom_inc = t.set_zoom_inc(mid)

                    t.snap_offset_mid()
                    break

            p.usleep(delay) # microseconds

class tws_Trade:
    tml = { } # stores all trades, indexed by tws orderId
    valid_id = -1
    
    def __init__(self, target, price_int, trade_type, is_stop, place = True):
        L = ladderex 
        self.spc_descriptor = ''

        self.offset = price_int
        self.price  = price_int / 100

        # get order size from form
        size = L.size_form.text()
        if not len(size): size = 0

        self.size = int(size)
        self.filled = 0
        self.avg    = 0.0

        # create order
        order_id = tws_Trade.valid_id
        tws_Trade.valid_id += 1

        order = Order()
        order.totalQuantity = Decimal(self.size)

        if trade_type   == 'S': order.action = 'SELL'
        elif trade_type == 'B': order.action = 'BUY'

        if is_stop:
            order.orderType = 'STP'
            order.auxPrice  = self.price
        else:
            order.orderType = 'LMT'
            order.lmtPrice  = self.price
            if target.ct_type != 'opt': order.outsideRth = True
        
        self.is_stop = is_stop

        self.order  = order
        self.id     = order_id
        self.status = 'init' # 'ok' -> 'live' -> 'cancelled' or full_fill' and various diags  

        self.inst = target
        self.trade_type = trade_type
        
        tws_Trade.tml[order_id] = self

        if place:
            # try to submit order
            ibapp.placeOrder(order_id, target.ct, order)
            L.click_indicator.append([self, time.time(), 'submit'])
        else:
            target.trades.append(self) # show the unplaced 'spc' status trade

class tws_Instrument:
    workers = [] # throw threads in here

    # based on the midpoint, set zoom increments and default zoom
    def set_zoom_inc(self, mid):
        t = self
        
        default = 1
        li = []

        if mid < 2*100:
            li = [ 1, 2, 5, 10 ]
        elif mid < 20*100:
            li = [ 1, 2, 5, 10, 20 ]
        elif mid < 100*100:
            li = [ 1, 2, 5, 10, 20 ]
        elif mid < 200*100:
            li = [ 1, 2, 5, 10, 20, 50 ] ; default = 2 
        elif mid < 400*100:
            li = [ 1, 2, 5, 10, 20, 50, 100 ] ; default = 5 
        elif mid < 2000*100:
            li = [ 1, 5, 10, 20, 50, 100, 200 ] ; default = 10 
        else:
            li = [ 1, 10, 20, 50, 100, 200, 500, 1000 ] ; default = 20 

        t.zoom_inc = li
        t.default_zoom = default 
        
        return default
    
    def snap_offset_mid(self):
        L = ladderex ; t = self

        mid = None
        if t.ask > 0 and t.bid > 0: 
            mid = ( t.ask + t.bid ) // 2
        elif t.ask   > 0: mid = t.ask
        elif t.bid   > 0: mid = t.bid
        elif t.last  > 0: mid = t.last
        elif t.close > 0: mid = t.close

        t.mpl_offset = mid + L.ladder_rows_mid * t.current_zoom_inc

        t.correct_oob()
        L.update()
    
    def correct_oob(self): # checks oob and ceils offset if needed so that it plays nice with zoom inc
        L = ladderex ; t = self
        
        t.mpl_offset = t.mpl_offset + (t.current_zoom_inc - t.mpl_offset) % t.current_zoom_inc
        
        if t.mpl_offset - L.ladder_rows * t.current_zoom_inc < 0:
            t.mpl_offset = (L.ladder_rows - 1) * t.current_zoom_inc
    
    def make_target(self):
        L = ladderex
        t = self

        L.size_form.setText(str(t.order_size))

        if t.ct_type == 'stock' or t.ct_type == 'mini':
            if t.ct_str in L.target_hist:
                L.target_hist.remove(t.ct_str)
            L.target_hist.append(t.ct_str)

        # handle tws data limit by maintaining [] of active mkt data streams oldest to newest
        mdt = L.mdata_tracker
        if t in mdt:
            mdt.remove(t) ; mdt.append(t)
        else:
            mdt.append(t)
            if not t.mdata_on:
                ibapp.reqMktData(t.ticker_id, t.ct, t.ticker_tl, False, False, []) ; t.mdata_on = True

            if len(mdt) == 50: # NOTE: increase this limit if you can
                old = mdt[:5]
                mdt = mdt[5:]
                for inst in old:
                    ibapp.cancelMktData(inst.ticker_id) ; inst.mdata_on = False

        if t.mpl_offset is not None:
            t.snap_offset_mid()
        L.target = self
        L.update()
    
    def __init__(self, ct_str, shadow = False, starred = False):
        self.ct_str = ct_str

        self.shadow  = shadow
        self.starred = starred # save on close
    
    def setup(self):
        L = ladderex 
        T = ladderex.target
        I = self

        cts = self.ct_str

        replace_shadow = False
        if cts in L.iml:
            if not L.iml[cts].shadow or I.shadow:
                return L.iml[cts]
            else:
                # switch to existing shadow and complete it 
                replace_shadow = True
                I = L.iml[cts]
                I.shadow = False

        if '/' in cts:
            cts = '/' + cts.replace('/', '') ; I.ct_str = cts # edge case where more than one '/'
            I.ct_type = 'mini' # futures
        elif len(cts) == 21:
            I.ct_type = 'opt'
        else: 
            I.ct_type = 'stock'
        
        if I.shadow:
            if I.ct_type == 'stock' or I.ct_type == 'mini':
                I.name = cts 
                I.opt_list = [ ]
            elif I.ct_type == 'opt':
                n = cts[6:] 
                pname = cts[:6]
                pname = pname.replace(' ', '')

                strike = int(n[7:]) / 1000
                I.name = pname + ' ' + '{0:g}'.format(strike) + n[6:7]
                I.parent_name = pname 
                I.exp_str = '20' + n[:2] + ' ' + month_array[int(n[2:4])] + ' ' + n[4:6]

                # set option parent
                if I.parent_name in L.iml:
                    I.parent = L.iml[I.parent_name]
                else:
                    p = tws_Instrument(I.parent_name, True)
                    p = p.setup()
                    I.parent = p
                
                # add to parent opt_list
                I.parent.opt_list.append(I)

            L.iml[cts] = I
            return I

        tick_list = '236,595'
        ct = Contract()
        if I.ct_type == 'stock':
            ct.symbol   = cts 
            ct.secType  = 'STK'
            ct.exchange = 'SMART'
            ct.currency = 'USD'
            I.name   = cts
        elif I.ct_type == 'opt': # option setup (OCC)
            n = cts[6:] 
            pname = cts[:6]
            pname = pname.replace(' ', '')

            strike = int(n[7:]) / 1000
            I.name = pname + ' ' + '{0:g}'.format(strike) + n[6:7]
            I.parent_name = pname 
            I.exp_str = '20' + n[:2] + ' ' + month_array[int(n[2:4])] + ' ' + n[4:6]

            ct.secType = 'OPT'
            ct.localSymbol = cts
            ct.exchange = 'SMART'
            ct.currency = 'USD'
        elif I.ct_type == 'mini': # NOTE: I don't trade these... does it work?
            ct.symbol   = cts 
            ct.secType  = 'FUT';
            ct.exchange = 'CME';
            if cts == '/YM': ct.exchange = 'CBOT'
            tick_list = '236'

        L.ct_details = None
        L.ct_counter += 1
        ibapp.reqContractDetails(L.ct_counter, ct)
        while L.ct_details is None: # block until ct resolved
            time.sleep(0.001)
        
        if L.ct_details == 'failed': 
            I.shadow = True # make sure invalid tws_Instrument remains a shadow
            return None
        
        I.ct = L.ct_details

        # Instrument init
        I.bid  = -1
        I.ask  = -1
        I.last = -1
        I.close = -1
        I.short_fact = None 
        I.ten_vol = -1
        I.trades = [ ]
        
        I.init_ask = -1
        I.pos = '0'
        I.avg_cost = '0.0'

        I.tws_errors = [ ] 
        
        I.mpl_offset = None
        
        if I.ct_type == 'stock':
            I.order_size = 100 
            if not replace_shadow:
                I.opt_list = [ ]
        elif I.ct_type == 'mini':
            I.order_size = 1 
            dt = I.ct.lastTradeDate
            I.name = cts[1:] + ' \'' + dt[2:4] +' '+ dt[4:6] +' '+ dt[6:]
            I.opt_list = [ ]
        elif I.ct_type == 'opt':
            I.order_size = 5

            if not replace_shadow:
                # set option parent
                if I.parent_name in L.iml:
                    I.parent = L.iml[I.parent_name]
                else:
                    p = tws_Instrument(I.parent_name, True)
                    p = p.setup()
                    I.parent = p

                # add I to parent's opt_list
                I.parent.opt_list.append(I)

        k = L.iml_idx_counter
        L.iml_idx[k] = I
        L.iml[cts]   = I
        
        th = ladderWorker()
        th.target = I
        th.start()
        I.workers.append(th)
        
        ibapp.reqMktData(k, L.ct_details, tick_list, False, False, []) ; I.mdata_on = True
        I.ticker_tl  = tick_list
        I.ticker_id  = k
        L.ct_details = None
        L.iml_idx_counter += 1
        
        # get existing position from pos_dict or add a blank entry
        cid = I.ct.conId
        if cid in ibpos_dict:
            ibpos_dict[cid][2] = I
            I.pos      = ibpos_dict[cid][0]
            I.avg_cost = ibpos_dict[cid][1]
        else:
            ibpos_dict[cid] = [ '0', '0.0', I ]

        return I
    
class widgetLadder(QWidget):
    # dims
    ladder_row_spacing  = 18
    ladder_height       = ladder_row_spacing * 29
    ladder_width        = 200 
    ladder_ctrl_height  = 60 # ladder begins on px 61
    ladder_bot_pane_h   = 20
    ladder_win_height   = ladder_height + ladder_ctrl_height + ladder_bot_pane_h
    ladder_win_width    = 200
    ladder_rows         = 29 ; ladder_rows_mid = ladder_rows // 2
    fill_pane_width     = 26
    
    wheel_focus_click = False

    # repaint will set these, used for main thread inputs mostly
    last_ask_pos         = 0
    last_bid_pos         = 0 
    last_price_box_x     = 0
    last_price_box_width = 0
    buy_bxs  = { }
    sell_bxs = { }

    price_rows = [0] * ladder_rows
    # NOTE: the price displayed should always be the price for the row you click
    # array is zeroed at start of each repaint for some degree of safety
        
    # signals and slots
    tick_signal      = pyqtSignal(int, int, float)
    pos_signal       = pyqtSignal(int, str, str)
    order_signal     = pyqtSignal(int, str, int, float)
    sockmsg_signal   = pyqtSignal(str, str)
    order_change_signal = pyqtSignal(int, str, float, Order)
    
    def sockmsg_slot(self, msg, msg_type):
        T = self.target
        if msg_type == 'load_inst':
            s = tws_Instrument(msg, False)
            s = s.setup()
            if s: 
                s.make_target()
            else:
                print('got bad socket msg:', msg_type, msg)

    def pos_slot(self, cid, pos_str, avg_str):
        # update position dict 
        if cid in ibpos_dict:
            P = ibpos_dict[cid]
            P[0] = pos_str
            P[1] = avg_str
            if P[2] is not None:
                P[2].pos      = pos_str
                P[2].avg_cost = avg_str

                if P[2] == ladderex.target: ladderex.update()
        else:
            ibpos_dict[cid] = [ pos_str, avg_str, None ] 

    def order_slot(self, order_id, status, filled, avg):
        import datetime
        L = self
        P = L.activated_floating_panel
        trade = tws_Trade.tml[order_id]

        # order error
        if filled == -1:
            now = datetime.datetime.now()
            time_str = now.strftime('%H:%M:%S')
            trade.inst.tws_errors.insert(0, [time_str, status])
        
            if trade.inst == L.target: L.update()
            return

        if status == 'PreSubmitted':
            trade.status = 'ok'
            if trade not in (l:=trade.inst.trades): l.append(trade)

        elif status == 'Cancelled':
            trade.status = 'cancelled'
            trade not in (l:=trade.inst.trades) or l.remove(trade)

        elif status == 'Submitted':
            trade.status = 'live'
            if trade not in (l:=trade.inst.trades): l.append(trade)

        if trade.filled != filled:
            trade.filled = filled
            trade.avg = avg
            if trade.size - trade.filled == 0:
                trade.status = 'full_fill'
                trade not in (l:=trade.inst.trades) or l.remove(trade)
                L.fill_indicator.append([trade, time.time(), 'full'])
            else:
                L.fill_indicator.append([trade, time.time(), 'part'])
            
        if trade.inst == L.target: L.update()

    def order_change_slot(self, order_id, change:str, n, order):
        L = self
        P = L.activated_floating_panel
        t = tws_Trade.tml[order_id] ; trade = t

        if change == 'size':
            t.size = int(n)
            print(trade.inst.name, 'order size change')
            if t.size - t.filled == 0: 
                # mostly for case when oca order is reduced in size and is now filled
                t.status = 'full_fill_adj' 
                trade not in (l:=trade.inst.trades) or l.remove(trade)

        elif change == 'price':
            t.price = n
            t.offset = int(t.price * 100)

        if t.inst == L.target: L.update()
    
    def tick_slot(self, ml_index, tick_type, n):
        if n <= 0 and tick_type != 46: return
        L = self
        t = L.iml_idx[ml_index]
        if   tick_type == 1:
            L.iml_idx[ml_index].bid     = int(n * 100)
        elif tick_type == 2:
            L.iml_idx[ml_index].ask     = int(n * 100)
            # recal zoom_inc array if ask doubled since set
            if t.ask / t.init_ask >= 2.0:
                t.init_ask = t.ask ; t.set_zoom_inc(t.ask)
        elif tick_type == 4:
            L.iml_idx[ml_index].last    = int(n * 100)
        elif tick_type == 9:
            L.iml_idx[ml_index].close   = int(n * 100)
        elif tick_type == 65: # 10 min volume
            L.iml_idx[ml_index].ten_vol = n
        elif tick_type == 46: #short factor
            L.iml_idx[ml_index].short_fact = n
        
        if L.target == t:
            L.update()
    
    def size_form_submit(self):
        L = self
        T = self.target
        size = L.size_form.text()
        
        if T is not None: T.order_size = int(size)

        L.size_form.clearFocus()
    
    def __init__(self):
        super().__init__()
        L = self
        L.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent) # must do fills

        self.setFixedSize(L.ladder_win_width, L.ladder_win_height)
        self.move(1000, 200)
        self.setWindowTitle('Quick Trader')

        # QAPP GLOBALS
        L.iml     = { } # instrument master list
        L.iml_idx = { } # indexed by tick_index
        L.iml_idx_counter = 99
        L.target = None
        L.target_hist = [ ]
        L.mdata_tracker = [ ]
        # briefly display click indicators for orders placed/deleted
        # and fill indicators in fill pane (partial in green, full in black)
        L.click_indicator = [ ]
        L.fill_indicator  = [ ]

        L.ct_counter = 999
        L.ct_details = None

        L.delete_mode   = False
        L.order_overlap = False # if true when order is clicked place a new order
        
        L.win_id = int(self.winId())
        
        L.ctrl_pane_font = QFont("Consolas", 11)

        L.bid_hl  = QColor(255,243,133)
        L.ask_hl  = QColor(129,210,125)
        L.last_hl = QColor(255,204,0)

        # init tws api thread and worker threads 
        try:
            ibapp.connect("127.0.0.1", 7497, clientId=0)
        except:
            print('ibapp.connect failed') ; exit()

        th = basicWorker()
        th.name = 'ibworker'
        th.start()
        L.tws_thread = th
        
        th = basicWorker()
        th.name = 'socketworker'
        th.start()
        L.socket_thread = th

        L.trade_check_busy = False
        
        # do some drawing while waiting for tws session ART
        blank_canvas = QPixmap(QSize(9999, 9999))
        L.blank   = blank_canvas
        L.blank_r = blank_canvas.rect()

        L.bt_decor = QPixmap(QSize(L.ladder_width, L.ladder_height))
        L.bt_decor.fill(QColor(240, 240, 240, 255))
        
        L.p_decor = QPixmap(QSize(L.ladder_width, L.ladder_height))
        L.p_decor.fill(QColor(240, 240, 240, 255))
        
        L.bid_arrow = QPixmap(QSize(16, 16))
        L.bid_arrow.fill(QColor(0, 0, 0, 0))
        L.ask_arrow = QPixmap(QSize(16, 16))
        L.ask_arrow.fill(QColor(0, 0, 0, 0))
        
        L.bot_pane = QPixmap(QSize(L.ladder_win_width, L.ladder_bot_pane_h))
        L.bot_pane.fill(QColor(180, 210, 210))
        
        L.ctrl_pane = QPixmap(QSize(L.ladder_win_width, L.ladder_ctrl_height))
        L.ctrl_pane.fill(QColor(180, 210, 210))
        
        L.fill_pane = QPixmap(QSize(L.fill_pane_width, L.ladder_height))
        L.fill_pane.fill(QColor(144, 180, 147))
        
        L.fill_pane_dm = QPixmap(QSize(L.fill_pane_width, L.ladder_height))
        L.fill_pane_dm.fill(QColor(196, 30, 58))
        
        L.mult_trades = QPixmap(QSize(12, 12))
        L.mult_trades.fill(QColor(0, 0, 0, 0))

        L.stop_hex = QPixmap(QSize(L.ladder_row_spacing - 1, L.ladder_row_spacing - 1))
        L.stop_hex.fill(QColor(255, 255, 255, 0))
        
        L.spc_trigger = QPixmap(QSize(8, 12))
        L.spc_trigger.fill(QColor(0, 0, 0, 0))
        
        # get width and height for spc icons
        qp = QPainter()
        qp.begin(blank_canvas)
        qp.setFont(QFont("Lucidia Console", 8, QFont.Weight.Bold))

        br = qp.boundingRect(L.blank_r, 0, 'TRIG')
        L.spc_icon_trigger = QPixmap(br.width() + 2, 10)
        
        br = qp.boundingRect(L.blank_r, 0, 'TARG')
        L.spc_icon_target  = QPixmap(br.width() + 2, 10)
        qp.end()

        L.spc_icon_group = QPixmap(20, 10)
        
        L.spc_icon_trigger.fill(QColor(147 + 20, 112 + 20, 219 + 20))
        L.spc_icon_target.fill(QColor(147 + 20, 112 + 20, 219 + 20))
        L.spc_icon_group.fill(QColor(147 + 20, 112 + 20, 219 + 20))
        
        fm = QFontMetrics(L.ctrl_pane_font)
        L.parent_arrow_w = 30
        L.parent_arrow_h = fm.ascent()
        L.parent_arrow = QPixmap(QSize(L.parent_arrow_w, L.parent_arrow_h))
        L.parent_arrow.fill(QColor(0, 0, 0, 0))
        L.last_parent_arrow_w = 0

        L.price_missing_warn = QPixmap(QSize(16, 16))
        L.price_missing_warn.fill(QColor(0,0,0,0))

        L.indicator_left = QPixmap(QSize(6, L.ladder_row_spacing - 1))
        L.indicator_left.fill(QColor(0,0,0,0))
        L.indicator_right = QPixmap(QSize(6, L.ladder_row_spacing - 1))
        L.indicator_right.fill(QColor(0,0,0,0))
        L.indicator_delete = QPixmap(QSize(60, L.ladder_row_spacing - 1))
        L.indicator_delete.fill(QColor(0,0,0,0))

        # paint graphics 
        qp.begin(L.spc_icon_trigger)
        qp.setFont(QFont("Lucidia Console", 8, QFont.Weight.Bold))
        qp.drawText(1, L.spc_icon_trigger.height(), 'TRIG')
        qp.end()
        
        qp.begin(L.spc_icon_target)
        qp.setFont(QFont("Lucidia Console", 8, QFont.Weight.Bold))
        qp.drawText(1, L.spc_icon_trigger.height(), 'TARG')
        qp.end()
        
        qp.begin(L.spc_icon_group)
        qp.fillRect(2,2,6,6, QColor(10,10,10))
        qp.fillRect(0,4,20,2, QColor(10,10,10))
        qp.fillRect(20 - 8,2,6,6, QColor(10,10,10))
        qp.end()
        
        qp.begin(L.spc_trigger)
        clr = QColor(54,15,90)
        qp.fillRect(0, 0, 8, 2, clr)
        qp.fillRect(3, 0, 2, 4, clr)
        qp.fillRect(0, 4, 8, 6, clr)
        qp.end()

        qp.begin(L.indicator_left)
        clr = QColor(0,0,0)
        qp.fillRect(0, 0, 2, L.ladder_row_spacing - 1, clr)
        qp.fillRect(2, 0, 4, 2, clr)
        qp.fillRect(2, L.ladder_row_spacing - 3, 4, 2, clr)
        qp.end()
            
        qp.begin(L.indicator_right)
        w = L.indicator_right.width()
        qp.fillRect(w - 2, 0, 2, L.ladder_row_spacing - 1, clr)
        qp.fillRect(w - 6, 0, 4, 2, clr)
        qp.fillRect(w - 6, L.ladder_row_spacing - 3, 4, 2, clr)
        qp.end()
        
        qp.begin(L.indicator_delete)
        w = 60
        qp.fillRect(w - 2, 0, 2, L.ladder_row_spacing - 1, clr)
        qp.fillRect(w - 6, 0, 4, 2, clr)
        qp.fillRect(w - 6, L.ladder_row_spacing - 3, 4, 2, clr)

        qp.setFont(QFont("Helvetica", 8, QFont.Weight.Bold))
        br = qp.boundingRect(L.blank_r, 0, 'del  ')
        qp.fillRect(w - br.width() - 2,  2, br.width(), L.ladder_row_spacing - 1 - 4, QColor(209,79,255))
        qp.setPen(QColor(0, 0, 0))
        qp.drawText(w - br.width(), 12, 'del')
        qp.end()
        
        step = L.ladder_row_spacing 
        decor_line_color = QColor(210, 210, 210)
        qp.begin(self.bt_decor)
        for i in range(L.ladder_rows):
            if i % 2:
                qp.fillRect(0, i * step, L.ladder_width, step, QColor('#BABABA'))
            else:
                qp.fillRect(0, i * step, L.ladder_width, step, QColor('#C0C0C0'))
        qp.end()
        
        qp.begin(self.p_decor)
        for i in range(L.ladder_rows):
            if i % 2:
                qp.fillRect(0, i * step, L.ladder_width, step, QColor('#D4D5C2'))
            else:
                qp.fillRect(0, i * step, L.ladder_width, step, QColor('#DBDCC8'))
        qp.end()
        
        qp.begin(self.bid_arrow)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        qp.setBrush(L.bid_hl)
        path = QPainterPath()
        path.moveTo(0, 0)
        path.lineTo(12, 8)
        path.lineTo(0, 16)
        path.lineTo(0, 0)
        qp.drawPath(path)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        qp.fillRect(0, 0, 1, 16, QColor(5,5,5)) # small visual adj 
        qp.end()
        
        qp.begin(self.ask_arrow)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        qp.setBrush(L.ask_hl)
        path = QPainterPath()
        path.moveTo(0, 0)
        path.lineTo(12, 8)
        path.lineTo(0, 16)
        path.lineTo(0, 0)
        qp.drawPath(path)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        qp.fillRect(0, 0, 1, 16, QColor(5,5,5)) # small visual adj 
        qp.end()
        L.ask_arrow = L.ask_arrow.transformed(QTransform().scale(-1, 1))
        
        qp.begin(L.parent_arrow)
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(QColor(180 - 40, 210 - 40, 210 - 40, 255))
        path = QPainterPath()
        path.moveTo(0, L.parent_arrow_h / 2)
        path.lineTo(8, L.parent_arrow_h)
        path.lineTo(L.parent_arrow_w, L.parent_arrow_h)
        path.lineTo(L.parent_arrow_w, 0)
        path.lineTo(8, 0)
        path.moveTo(0, L.parent_arrow_h / 2)
        qp.drawPath(path)
        qp.end()
        
        qp.begin(L.price_missing_warn)
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(QColor(236, 88, 0))
        path = QPainterPath()
        path.moveTo(16 / 2, 0)
        path.lineTo(16, 16)
        path.lineTo(0, 16)
        path.lineTo(16 / 2, 0)
        qp.drawPath(path)
        qp.setFont(QFont("Helvetica", 8, QFont.Weight.Bold))
        qp.setPen(QColor(0, 0, 0))
        qp.drawText(5, 15, '?')
        qp.end()
        
        qp.begin(L.mult_trades)
        qp.setFont(QFont("Helvetica", 14, QFont.Weight.Bold))
        qp.setPen(QColor(230, 230, 230))
        qp.drawText(0, 16, '*')
        qp.end()
        
        qp.begin(L.stop_hex)
        d = L.ladder_row_spacing - 1
        L.stop_hex_mid = d // 2
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(QColor(40, 40, 40, 255))
        
        path = QPainterPath()
        path.moveTo(4, 0)
        path.lineTo(d - 5, 0)
        
        path.lineTo(d, 5)
        path.lineTo(d, d - 4)

        path.lineTo(d, 4)
        path.lineTo(d, d - 5)
        
        path.lineTo(d - 4, d)
        path.lineTo(4, d)
        
        path.lineTo(0, d - 4)
        path.lineTo(0, 4)

        qp.drawPath(path)

        qp.end()
        
        # init switch buttons in control panel
        L.sbutton_wh = 24
        
        L.sbutton_opt_x = L.ladder_win_width - L.sbutton_wh - 4
        L.sbutton_opt_y = L.ladder_ctrl_height - L.sbutton_wh  - 4
        L.sbutton_opt_pm = QPixmap(QSize(L.sbutton_wh, L.sbutton_wh))
        L.sbutton_opt_pm.fill(QColor(0, 0, 0, 0))
        
        L.sbutton_tbox_x = L.ladder_win_width - L.sbutton_wh * 2 - 8
        L.sbutton_tbox_y = L.ladder_ctrl_height - L.sbutton_wh - 4
        L.sbutton_tbox_pm = QPixmap(QSize(L.sbutton_wh, L.sbutton_wh))
        L.sbutton_tbox_pm.fill(QColor(0, 0, 0, 0))
        
        qp.begin(L.sbutton_opt_pm)
        qp.setFont(QFont("Helvetica", 18, QFont.Weight.Bold))
        qp.setPen(QColor(210,221,146)) ; qp.drawText(1, 20, 'O')

        qp.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        qp.setPen(QColor(30,30,30)) ; qp.drawText(6, 22, 'P')

        qp.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        qp.setPen(QColor(60,60,60)) ; qp.drawText(15, 16, 'T')
        qp.end()
        
        qp.begin(L.sbutton_tbox_pm)
        qp.fillRect(2, 4, L.sbutton_wh - 4, L.sbutton_wh - 8, QColor("#C20004"))
        qp.fillRect(3, 4 + L.sbutton_wh // 4, L.sbutton_wh - 5, 1, QColor(20,20,20))
        qp.fillRect(L.sbutton_wh // 2, 3 + L.sbutton_wh // 4, 4, 4, QColor(20,20,20))
        qp.end()
        
        qp.begin(L.ctrl_pane)
        qp.fillRect(L.sbutton_opt_x, L.sbutton_opt_y, L.sbutton_wh, L.sbutton_wh, QColor(180 - 44, 210 - 44, 210 - 44, 255))
        qp.fillRect(L.sbutton_tbox_x, L.sbutton_tbox_y, L.sbutton_wh, L.sbutton_wh, QColor(180 - 44, 210 - 44, 210 - 44, 255))
        qp.end()
        
        qp.begin(L.fill_pane_dm) # fill pane graphic for delete mode
        lock_clr = QColor(25, 25, 35)
        lock_y   = L.ladder_height - 24 
        lock_x   = L.fill_pane_width - 16 
        qp.fillRect(lock_x,     lock_y + 10, 14, 12, lock_clr) 
        qp.fillRect(lock_x + 2, lock_y + 4,  3,  6,  lock_clr)
        qp.fillRect(lock_x + 9, lock_y + 4,  3,  6,  lock_clr)
        qp.fillRect(lock_x + 2, lock_y + 4,  8,  3,  lock_clr)
        qp.end()
        
        # order size editor
        ql = QLineEdit(self)
        ql.setFont(QFont("Arial", 10))
        ql.setPlaceholderText('Size')
        ql.setMaxLength(5)
        ql.setMaximumSize(62, 30)
        ql.move(L.ladder_win_width - 66, 4)
        
        rx = QRegularExpression(R'\d+')
        ql.setValidator(QRegularExpressionValidator(rx))
        ql.editingFinished.connect(self.size_form_submit)
        ql.show()
        L.size_form = ql

        L.fp_width = int(L.ladder_width * 0.9)
        L.activated_floating_panel     = None
        L.activated_floating_panel_ext = None # rendered after main panel for special modes
                                              # passes most input through and has a close button

        L.setFocusPolicy(Qt.FocusPolicy.WheelFocus) # needed to grab focus from input box
        L.setFocus() # take focus from size input box
        
        while tws_Trade.valid_id == -1: # wait for api session
            print('.. ', end='')
            time.sleep(0.3)
        print('tws connected')

        ibapp.reqPositions()
        
        L.tick_signal.connect(self.tick_slot)
        L.order_signal.connect(self.order_slot)
        L.order_change_signal.connect(self.order_change_slot)
        L.pos_signal.connect(self.pos_slot)
        L.sockmsg_signal.connect(self.sockmsg_slot)
        
        L.trade_check = QTimer(self)
        L.trade_check.timeout.connect(self.trade_check_tick)
        
        L.show()

    # load .save
    def load_save(self):
        L = self

        s = None
        save_file = open('qct.save', 'a+') ; save_file.seek(0)
        sf_lines = save_file.readlines()
        for l in sf_lines:
            splt = l.rstrip('\n').split(':')
            key = splt[0]
            val = splt[1]
            if key == 'LAST':  # last open instrument
                s = tws_Instrument(val, False)
                s = s.setup()
            elif key == 'SOPT': # starred option
                t = tws_Instrument(val, True, True)
                t.setup()

        if s: 
            s.make_target()
        
        if True: # NOTE: enable to stop misclicks on start
            L.delete_mode = True
        
        L.trade_check.start(1000) # ms
        L.update()
    
    def closeEvent(self, event):
        L = self
        L.trade_check.stop()
        print('exiting')
        ibapp.disconnect()
        L.tws_thread.terminate()
        for w in tws_Instrument.workers: w.terminate()
        time.sleep(0.05) # wait a bit for threads to end
        
        save_str = '' # save starred options and last target
        for s in L.iml.values():
            if s.ct_type != 'opt':
                for o in s.opt_list:
                    if o.starred:
                        save_str += 'SOPT:' + o.ct_str + '\n'
        
        if self.target:
            save_str += 'LAST:' + self.target.ct_str + '\n' 

        save_file = open('qct.save', 'w')
        save_file.write(save_str)

        event.accept()
    
    def focusOutEvent(self, e):
        self.trade_check.setInterval(330)
    def focusInEvent(self, e):
        self.trade_check.setInterval(330 * 4)

    def trade_check_tick(self):
        L = self
        
        # check for order changes in tws (size or price) 
        if ibapp.open_order_req_ended:
            ibapp.open_order_req_ended = False
            ibapp.reqOpenOrders()

    def paintEvent(self, e):
        L = self
        T = self.target
        qp = QPainter()
        qp.begin(self)

        P = L.activated_floating_panel ; P_ext = L.activated_floating_panel_ext
        
        if e.region().boundingRect().width() < L.ladder_win_width:
            # qbuttons trigger this on hover etc
            return
        
        L.price_rows = [0] * L.ladder_rows ; L.buy_bxs  = { } ; L.sell_bxs = { }
        
        qp.drawPixmap(0, 0, L.ctrl_pane)
        qp.drawPixmap(0, L.ladder_ctrl_height, L.bt_decor)
        
        if L.delete_mode == True:
            qp.drawPixmap(L.ladder_win_width - L.fill_pane_width, L.ladder_ctrl_height, L.fill_pane_dm)
        else:
            qp.drawPixmap(L.ladder_win_width - L.fill_pane_width, L.ctrl_pane.height(), L.fill_pane)
        
        qp.drawPixmap(0, L.ladder_win_height - L.ladder_bot_pane_h, L.bot_pane)
        qp.drawPixmap(L.sbutton_opt_x, L.sbutton_opt_y, L.sbutton_opt_pm)
        qp.drawPixmap(L.sbutton_tbox_x, L.sbutton_tbox_y, L.sbutton_tbox_pm)
        
        if T is None:
            try: L.save_loaded
            except:
                L.save_loaded = QTimer.singleShot(0, L.load_save)
                qp.drawText(4, L.ladder_ctrl_height + L.ladder_row_spacing - 2, 'Loading save..')

            if P: # draw floating panel if there is one
                for n, g in P.graphics.items():
                    qp.drawPixmap(g[1], g[2], g[0])
            qp.end()
            return
        
        qp.setFont(L.ctrl_pane_font)
        
        # draw instrument name and position
        if T.ct_type == 'opt':
            name_br = qp.boundingRect(L.blank_r, 0, T.name)
            
            fill = QColor(180 - 20, 210 - 20, 210 - 20, 255)
            qp.fillRect(0, 4, name_br.width()  + 20, L.parent_arrow_h, fill)
            qp.drawPixmap(int(name_br.width()) + 10, 4, L.parent_arrow)

            L.last_parent_arrow_w = name_br.width() + 6 + L.parent_arrow_w
        
        qp.drawText(4, 4 + 12, T.name)
        
        qp.drawText(4, L.ladder_ctrl_height - 4, T.pos + ' ' + T.avg_cost)
        
        if T.ct_type == 'opt':
            qp.setFont(QFont("Consolas", 9))
            qp.drawText(4, 4 + 24, T.exp_str)
        
        if T.mpl_offset is None:
            if P: # draw floating panel if there is one
                for n, g in P.graphics.items():
                    qp.drawPixmap(g[1], g[2], g[0])
            qp.end()
            return
                
        bot_pane_off = 0
        if T.short_fact is not None:
            bot_pane_off = 16
            if T.short_fact > 2.5:
                qp.fillRect(0, L.ladder_win_height - L.ladder_bot_pane_h, bot_pane_off, L.ladder_bot_pane_h, QColor('#5CED73'))
            elif T.short_fact > 1.5:
                qp.fillRect(0, L.ladder_win_height - L.ladder_bot_pane_h, bot_pane_off, L.ladder_bot_pane_h, QColor('#008631'))
            else:
                qp.fillRect(0, L.ladder_win_height - L.ladder_bot_pane_h, bot_pane_off, L.ladder_bot_pane_h, QColor(100,100,100))
        
        qp.setFont(L.ctrl_pane_font)
        qp.setPen(QColor(250, 250, 250))

        if T.close > 0 and T.last > 0:
            diff = (T.last / T.close - 1) * 100 
            diff_str = str(format(abs(diff), '.2f')) + '%'
            
            diff_br = qp.boundingRect(L.blank_r, 0, diff_str)
            if diff < 0:
                diff *= -1
                qp.fillRect(bot_pane_off, L.ladder_win_height - L.ladder_bot_pane_h, diff_br.width() + 4, L.ladder_bot_pane_h, QColor(147, 28, 29))
            else:
                qp.fillRect(bot_pane_off, L.ladder_win_height - L.ladder_bot_pane_h, diff_br.width() + 4, L.ladder_bot_pane_h, QColor(40, 40, 40))
            bot_pane_off += 2
            qp.drawText(bot_pane_off, L.ladder_win_height - 3, diff_str)
            bot_pane_off += diff_br.width() + 2
        
        if T.ten_vol >= 1000:
            vol_str = str(int(T.ten_vol / 1000)) + 'K'
            vol_br = qp.boundingRect(L.blank_r, 0, vol_str)
            qp.fillRect(bot_pane_off, L.ladder_win_height - L.ladder_bot_pane_h, vol_br.width() + 4, L.ladder_bot_pane_h, QColor('#660033'))
            bot_pane_off += 2
            qp.drawText(bot_pane_off, L.ladder_win_height - 4, vol_str)
            bot_pane_off += vol_br.width()
        
        qp.setFont(QFont("Arial", 12, QFont.Weight.DemiBold))
        qp.setPen(QColor(0, 0, 0))

        if T.current_zoom_inc > T.zoom_inc[0]:
            br = qp.boundingRect(L.blank_r, 0, '± ' + str(T.current_zoom_inc))
            qp.drawText(L.ladder_win_width - br.width() - 2, L.ladder_win_height - 2, '± ' + str(T.current_zoom_inc))
        
        font      = QFont("Arial", 9)
        font_bold = QFont("Arial", 9, QFont.Weight.DemiBold)

        o = T.mpl_offset
        m = L.ladder_row_spacing
        
        # calculate price box x and width based on largest string relevant 
        qp.setFont(font_bold)
        r_max = qp.boundingRect(L.blank_r, 0, '9999.99')
        x_off = int(L.ladder_win_width / 2 - r_max.width() / 2) - 8
        
        if   o < 1000:
             r = qp.boundingRect(L.blank_r, 0, '9.99')
        elif o < 10000:
             r = qp.boundingRect(L.blank_r, 0, '99.99')
        elif o < 100000:
             r = qp.boundingRect(L.blank_r, 0, '999.99')
        else:
             r = r_max 
        
        pb_width = int(r.width()) + 8
        
        fm = QFontMetrics(font_bold)

        y_off = m / 2 - fm.ascent() / 2 - L.ladder_ctrl_height
        
        qp.drawPixmap(x_off, L.ladder_ctrl_height, L.p_decor, 0, 0, pb_width, L.ladder_height)
        
        # calculate bid, ask, and last pos
        na = 0 ; nb = 0 ; nl = 0 ; nl_clr = L.last_hl

        ask_adj = 0 # since ask is cieled and bid is floored, if needed
                    # offset ask and bid arrows toward the true number
        if T.ask > 0:
            nrm_ask = T.ask % T.current_zoom_inc
            if nrm_ask == 0:
                nrm_ask = T.current_zoom_inc
            else:
                ask_adj = L.ladder_row_spacing // 2 + 1

            na = T.ask + (T.current_zoom_inc - nrm_ask) # ciel
        else:
            qp.drawPixmap(bot_pane_off + 6 , L.ladder_win_height - 18, L.price_missing_warn)

        bid_adj = 0
        if T.bid > 0:
            nrm_bid = T.bid % T.current_zoom_inc
            if nrm_bid != 0:
                bid_adj = L.ladder_row_spacing // 2 - 1
            nb = T.bid - nrm_bid # floor
        else:
            qp.drawPixmap(bot_pane_off + 6 , L.ladder_win_height - 18, L.price_missing_warn)

        if T.last > 0:
            nrm_last = T.last % T.current_zoom_inc
            nl = T.last - nrm_last # floor
        elif T.close > 0: 
            # try to display close if last is missing
            nrm_last = T.close % T.current_zoom_inc
            nl = T.close - nrm_last # floor
            nl_clr = QColor(208, 122, 129)
        
        L.last_price_box_width = pb_width
        L.last_price_box_x     = x_off

        nb_hit = None 
        na_hit = None 

        # populate boxes for trades, indicators, fills (boxes are all ceiled)
        boxes = { }        
        for r in T.trades:
            ceil_offset = r.offset + (T.current_zoom_inc - r.offset) % T.current_zoom_inc
            
            if ceil_offset not in boxes: boxes[ceil_offset] = { }
            trade_row = boxes[ceil_offset]
            
            if r.trade_type == 'B':
                if 'buy' in trade_row:
                    trade_row['buy'].append(r)
                else:
                    trade_row['buy'] = [ r ]
            
            elif r.trade_type == 'S':
                if 'sell' in trade_row:
                    trade_row['sell'].append(r)
                else:
                    trade_row['sell'] = [ r ]
            
        for ci in L.click_indicator.copy():
            trade = ci[0] ; start_time = ci[1]
            
            if time.time() - start_time > 2.0: # fade time
                L.click_indicator.remove(ci)
            if trade.inst != L.target: continue
            
            ceil_offset = trade.offset + (T.current_zoom_inc - trade.offset) % T.current_zoom_inc
            
            if ceil_offset not in boxes: boxes[ceil_offset] = { }
            trade_row = boxes[ceil_offset]
                
            if trade.trade_type == 'S':
                trade_row['ind_s'] = ci
            else:
                trade_row['ind_b'] = ci
            
        for f in L.fill_indicator.copy():
            trade = f[0] ; start_time = f[1] ; fill_type = f[2]
            
            if time.time() - start_time > 5.0:
                L.fill_indicator.remove(f)
            if trade.inst != L.target: continue
            
            ceil_offset = trade.offset + (T.current_zoom_inc - trade.offset) % T.current_zoom_inc
            
            if ceil_offset not in boxes: boxes[ceil_offset] = { }
            boxes[ceil_offset]['fill'] = fill_type
        
        # fill ladder with available price information
        bold_inc = T.current_zoom_inc * 5
        for i in range(L.ladder_rows):
            p = QPointF(x_off + 4, (i + 1) * m - y_off)
            y = i * m + L.ladder_ctrl_height
            
            if na == o:
                qp.fillRect(x_off, y, pb_width, L.ladder_row_spacing, L.ask_hl)
                L.last_ask_pos = i
                na_hit = i
            if nb == o:
                qp.fillRect(x_off, y, pb_width, L.ladder_row_spacing, L.bid_hl)
                L.last_bid_pos = i
                nb_hit = i
            if nl == o:
                qp.fillRect(x_off + 3, y, pb_width - 6, L.ladder_row_spacing, nl_clr)

            qp.setFont(font)
            if o % bold_inc == 0:
                qp.setFont(font_bold)
        
            pstr = f'{o / 100:.2f}'

            qp.drawText(p, pstr) # draws past box above 9999.99

            L.price_rows[i] = o
    
            # draw boxes for trades, indicators, fills
            if o in boxes:
                row_bxs = boxes[o]
                
                if 'buy' in row_bxs:
                    top = row_bxs['buy'][-1]
                    L.buy_bxs[i] = top
                    
                    color = QColor(130, 130, 130)
                    if top.status == 'live':
                        color = QColor(100, 100, 250)
                    elif top.status == 'spc':
                        color = QColor(147, 112, 219)
                        
                    qp.fillRect(0, y, L.last_price_box_x, L.ladder_row_spacing - 1, color)
                
                    if top.is_stop:
                        qp.drawPixmap(L.last_price_box_x // 2 - L.stop_hex_mid, y, L.stop_hex)
                    if len(row_bxs['buy']) > 1:
                        qp.drawPixmap(1, y, L.mult_trades)

                    if len(top.spc_descriptor):
                        qp.drawPixmap(L.last_price_box_x - 9, y + 2, L.spc_trigger)

                    if top.status == 'spc' and top.spc_icon is not None:
                        qp.drawPixmap(L.last_price_box_x - top.spc_icon.width(), y - 1, top.spc_icon)

                if 'sell' in row_bxs:
                    top = row_bxs['sell'][-1]
                    L.sell_bxs[i] = top
                    
                    l = L.ladder_width - L.last_price_box_x - L.last_price_box_width - L.fill_pane_width
                    
                    color = QColor(130, 130, 130)
                    if top.status == 'live':
                        color = QColor(250, 100, 100)
                    elif top.status == 'spc':
                        color = QColor(147, 112, 219)

                    qp.fillRect(L.last_price_box_x + L.last_price_box_width, y, l, L.ladder_row_spacing - 1, color)

                    if top.is_stop:
                        qp.drawPixmap(L.last_price_box_x + L.last_price_box_width + l // 2 - L.stop_hex_mid, y, L.stop_hex)
                    if len(row_bxs['sell']) > 1:
                        qp.drawPixmap(L.last_price_box_x + L.last_price_box_width + l - 12, y, L.mult_trades)
                    
                    if len(top.spc_descriptor):
                        os = L.last_price_box_x + L.last_price_box_width + 2
                        qp.drawPixmap(os, y + 2, L.spc_trigger)
                    
                    if top.status == 'spc' and top.spc_icon is not None:
                        qp.drawPixmap(L.last_price_box_x + L.last_price_box_width, y - 1, top.spc_icon)

                for box_type in [ 'ind_b', 'ind_s' ]:
                    if not box_type in row_bxs: continue
                    trade = row_bxs[box_type][0] ; dt = row_bxs[box_type][1] ; ci_type = row_bxs[box_type][2]

                    w = L.last_price_box_x
                    x = 0

                    if 's' in box_type: # sell box dims
                        x = L.last_price_box_x + L.last_price_box_width
                        w = L.ladder_width - L.last_price_box_x - L.last_price_box_width - L.fill_pane_width

                    alpha = 1 - (time.time() - dt) / 2.0 # fade time
                    qp.setOpacity(alpha)

                    if ci_type == 'submit':
                        qp.drawPixmap(x, y, L.indicator_left)
                        qp.drawPixmap(x + w - 6, y, L.indicator_right)
                    elif ci_type == 'delete':
                        qp.drawPixmap(x, y, L.indicator_left)
                        qp.drawPixmap(x + w - 6, y, L.indicator_right)
                        qp.drawPixmap(x + w - 60, y, L.indicator_delete)
                    
                    qp.setOpacity(1.0)
                    L.update() # update while indicator boxes are visible

                if 'fill' in row_bxs:
                    w = L.fill_pane_width - 8
                    x = L.ladder_width - L.fill_pane_width + 5
                    
                    if row_bxs['fill'] == 'part':
                        qp.fillRect(x, y, w, L.ladder_row_spacing - 4, QColor(159, 234, 9))
                    else:
                        qp.fillRect(x, y, w, L.ladder_row_spacing - 4, QColor(0, 0, 0))

            o -= T.current_zoom_inc

        # draw bid/ask arrows
        if na_hit is not None:
            if not ask_adj:
                qp.drawPixmap(x_off + pb_width-2, na_hit * m + L.ladder_ctrl_height, L.ask_arrow)
            else:
                qp.drawPixmap(x_off + pb_width-2, na_hit * m + L.ladder_ctrl_height + ask_adj, L.ask_arrow)
        elif na == 0:
            pass
        elif na <= o:
            aoff = L.ladder_rows
            mod = L.ladder_row_spacing // 2 
            qp.drawPixmap(x_off + pb_width-2, aoff * m + L.ladder_ctrl_height+1 - mod, L.ask_arrow)
        elif na > T.mpl_offset:
            aoff = 0
            mod = L.ladder_row_spacing // 2 
            qp.drawPixmap(x_off + pb_width-2, aoff * m + L.ladder_ctrl_height+1 - mod, L.ask_arrow)
        
        if nb_hit is not None:
            if not bid_adj:
                qp.drawPixmap(x_off - 14, nb_hit * m + L.ladder_ctrl_height, L.bid_arrow)
            else:
                qp.drawPixmap(x_off - 14, nb_hit * m + L.ladder_ctrl_height - bid_adj, L.bid_arrow)
        elif nb == 0:
            pass
        elif nb <= o:
            boff = L.ladder_rows
            mod = L.ladder_row_spacing // 2 
            qp.drawPixmap(x_off - 14, boff * m + L.ladder_ctrl_height+1 - mod, L.bid_arrow)
        elif nb > T.mpl_offset:
            boff = 0
            mod = L.ladder_row_spacing // 2 
            qp.drawPixmap(x_off - 14, boff * m + L.ladder_ctrl_height+1 - mod, L.bid_arrow)
            
        # draw floating panels
        if P_ext:
            for n, g in P_ext.graphics.items():
                qp.drawPixmap(g[1], g[2], g[0])
        if P:
            for n, g in P.graphics.items():
                qp.drawPixmap(g[1], g[2], g[0])
        
        qp.end()

    def keyPressEvent(self, e):
        L = self
        T = self.target
        P = L.activated_floating_panel
        P_ext = L.activated_floating_panel_ext
        
        key = e.key()
        md  = qapp.keyboardModifiers()
        
        # send keys to floating panel if open
        if P is not None:
            P.collision('back', 'key', key, e.text(), md) 
            return
        
        if P_ext is not None:
            res = P_ext.collision('back', 'key', key, e.text(), md) 
            # floating_panel.collision will return an enum if it wants to pass the press event on 
            if res is None: return
        
        if key == Qt.Key.Key_E.value: #E enter a new instrument
            L.activated_floating_panel = floating_panel('enter_inst')
            L.activated_floating_panel.prepare()
            L.update() ; return
        
        if key == Qt.Key.Key_T.value: #T for toolbox
            L.activated_floating_panel = floating_panel('toolbox', y=-1)
            L.activated_floating_panel.prepare()
            L.update() ; return
        
        if T is None: return
        
        if key == Qt.Key.Key_X.value: #X focus order size box
            L.size_form.clear()
            L.size_form.setFocus() ; return

        if key == Qt.Key.Key_D.value or ( L.delete_mode == True and key == Qt.Key.Key_Escape ): #D
            L.delete_mode ^= True
            L.update() ; return

        if key == Qt.Key.Key_O.value or key == Qt.Key.Key_S.value: #O
            L.activated_floating_panel = floating_panel('opt_switcher', y=-1)
            L.activated_floating_panel.prepare()
            L.update() ; return

        if key == Qt.Key.Key_B.value: #B go back to parent from option
            if T.ct_type == 'opt':
                s = tws_Instrument(T.parent.ct_str, False)
                s = s.setup()
                if s: s.make_target()
                return
        
        if T.mpl_offset is None: return
        
        if key == Qt.Key.Key_H.value: #H show order error helper
            L.activated_floating_panel = floating_panel('order_diag')
            L.activated_floating_panel.prepare() ; L.update() ; return
        
        if key == Qt.Key.Key_C.value: #C place one cancels all order
            L.activated_floating_panel_ext = floating_panel('oca_group_create')
            L.activated_floating_panel_ext.prepare()
            L.update() ; return
        
        if key == Qt.Key.Key_V.value: #V place conditional order(s)
            L.activated_floating_panel_ext = floating_panel('price_cond_create')
            L.activated_floating_panel_ext.prepare()
            L.update() ; return

        # navigate the ladder
        if key == Qt.Key.Key_K.value:   #K
            T.mpl_offset += 6 * T.current_zoom_inc
            T.correct_oob() ; L.update() ; return

        elif key == Qt.Key.Key_J.value: #J
            T.mpl_offset -= 6 * T.current_zoom_inc
            T.correct_oob() ; L.update() ; return

        elif key == Qt.Key.Key_M.value: #M
            T.snap_offset_mid()
            T.correct_oob() ; L.update() ; return
        
        elif key == Qt.Key.Key_F.value: #F
            T.current_zoom_inc = T.default_zoom
            T.snap_offset_mid()
            T.correct_oob() ; L.update() ; return
        
        if key == Qt.Key.Key_End.value: # print internals to console
            print('- - - diag - - -\n')
            for k, s in L.iml.items():
                print('instr:', int(s.shadow), s.ct_type, s.name)
                if not s.shadow and len(s.trades):
                    for t in s.trades: 
                        ax = '*' if t.status == 'spc' else ''
                        if t != s.trades[-1]: print(t.id, end=ax+',')
                        else: print(t.id, end=ax+'\n')

            for t in tws_Trade.tml.values():
                if t.status != 'spc': print('trade:', t.id, t.status)
            print('\n- - diag end - -') ; return

    def wheelEvent(self, e):
        T = self.target
        L = self 
        pos = e.position()
        if win32gui.GetForegroundWindow() != self.win_id: # WINDOWS
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
            L.wheel_focus_click = True
        
        if L.activated_floating_panel:
            P = L.activated_floating_panel
            for n, r in P.colrects.items():
                if pos.x() > r[0] and pos.x() < r[0] + r[2] and pos.y() > r[1] and pos.y() < r[1] + r[3]:
                    if e.angleDelta().y() > 0:
                        P.collision(n, 'scroll_up'); return
                    P.collision(n, 'scroll_down'); return
        
        if T is None or T.mpl_offset is None:
            return
        if pos.y() < L.ladder_ctrl_height or pos.y() > L.ladder_win_height - L.ladder_bot_pane_h:
            return

        # scroll event on ladder, scroll offset if in proper bounds
        if pos.x() < L.last_price_box_x or pos.x() > L.last_price_box_width + L.last_price_box_x:
            if e.angleDelta().y() < 0:
                T.mpl_offset -= 4 * T.current_zoom_inc

            elif e.angleDelta().y() > 0:
                T.mpl_offset += 4 * T.current_zoom_inc

            T.correct_oob()
            L.update()
            return
        
        # zoom ladder
        if e.angleDelta().y() < 0: # zoom out
            i = T.zoom_inc.index(T.current_zoom_inc)
            if i + 1 >= len(T.zoom_inc):
                return

            row = (pos.y() - L.ladder_ctrl_height) // L.ladder_row_spacing
            new_inc = T.zoom_inc[i+1]

            oset = T.mpl_offset - row * T.current_zoom_inc
            oset = oset - oset % new_inc

            T.mpl_offset = oset + row * new_inc
            T.current_zoom_inc = new_inc

        else: # zoom in
            i = T.zoom_inc.index(T.current_zoom_inc)
            if i - 1 < 0:
                return

            row = (pos.y() - L.ladder_ctrl_height) // L.ladder_row_spacing
            new_inc = T.zoom_inc[i-1]
            
            oset = T.mpl_offset - row * T.current_zoom_inc
            
            T.mpl_offset = oset + row * new_inc
            T.current_zoom_inc = new_inc
            

        T.correct_oob()
        L.update()
    
    def mousePressEvent(self, e):
        # try to focus window on scroll event
        if self.wheel_focus_click:
            self.wheel_focus_click = False
            return

        L = self
        T = self.target
        pos = e.position()
        
        # check if clicked on floating panels
        if L.activated_floating_panel:
            P = L.activated_floating_panel
            
            if P.ignore_oob: # send an oob click if not on 'back'; allows simplification of some panels
                r = P.colrects['back']
                if pos.x() > r[0] and pos.x() < r[0] + r[2] and pos.y() > r[1] and pos.y() < r[1] + r[3]:
                    pass
                else:
                    P.collision('out_of_bounds', 'click', e.button())
                    return

            for n, r in reversed(P.colrects.items()):
                if pos.x() > r[0] and pos.x() < r[0] + r[2] and pos.y() > r[1] and pos.y() < r[1] + r[3]:
                    ret = P.collision(n, 'click', e.button())
                    return

            # clicked outside of floating panel
            P.collision('out_of_bounds', 'click', e.button())
            return
        
        if L.activated_floating_panel_ext:
            P = L.activated_floating_panel_ext
            
            for n, r in reversed(P.colrects.items()):
                if pos.x() > r[0] and pos.x() < r[0] + r[2] and pos.y() > r[1] and pos.y() < r[1] + r[3]:
                    ret = P.collision(n, 'click', e.button())
                    if ret is None: return

        # clicked in top ctrl panel
        if pos.y() < L.ladder_ctrl_height + 1:
            if pos.y() > L.sbutton_tbox_y and pos.y() < L.sbutton_tbox_y + L.sbutton_wh and pos.x() > L.sbutton_tbox_x and pos.x() < L.sbutton_tbox_x + L.sbutton_wh:
                L.activated_floating_panel = floating_panel('toolbox')
                L.activated_floating_panel.prepare()
                L.update() ; return

            if not T: return

            if T.ct_type == 'opt':
                if pos.y() > 4 and pos.y() < L.parent_arrow_h + 4 and pos.x() < L.last_parent_arrow_w:
                    # LOAD
                    s = tws_Instrument(T.parent.name, False)
                    s = s.setup()
                    if s: s.make_target()
                    return
            
            if pos.y() > L.sbutton_opt_y and pos.y() < L.sbutton_opt_y + L.sbutton_wh and pos.x() > L.sbutton_opt_x and pos.x() < L.sbutton_opt_x + L.sbutton_wh:
                L.activated_floating_panel = floating_panel('opt_switcher')
                L.activated_floating_panel.prepare()
                L.update() ; return
            
            return
        
        if pos.y() > L.ladder_win_height - L.ladder_bot_pane_h - 1: return
        
        # clicked on fill pane to toggle delete mode
        if pos.x() > L.ladder_win_width - L.fill_pane_width:
            L.delete_mode ^= True
            L.update() ; return
        
        # click was in ladder
        row = int((pos.y() - L.ladder_ctrl_height) / L.ladder_row_spacing)

        if L.price_rows[row] is None: return

        # get the order type, check if clicked on existing trade
        md = qapp.keyboardModifiers()
        on  = None 
        stp = False 
        clicked_on_trade = None

        if pos.x() < L.last_price_box_x:
            if row in L.buy_bxs:
                clicked_on_trade = L.buy_bxs[row]
            
            if e.button() == Qt.MouseButton.LeftButton and md == Qt.KeyboardModifier.NoModifier:
                on = 'B'
            elif e.button() == Qt.MouseButton.LeftButton and md == Qt.KeyboardModifier.ControlModifier:
                on  = 'B'
                stp = True

        elif pos.x() > L.last_price_box_x + L.last_price_box_width:
            if row in L.sell_bxs:
                clicked_on_trade = L.sell_bxs[row]

            if e.button() == Qt.MouseButton.RightButton and md == Qt.KeyboardModifier.NoModifier:
                on = 'S'
            elif e.button() == Qt.MouseButton.LeftButton and md == Qt.KeyboardModifier.ControlModifier:
                on  = 'S'
                stp = True 
        
        if clicked_on_trade is not None:
            r = clicked_on_trade
            if r.status == 'spc':
                r.inst.trades.remove(r)
                if L.activated_floating_panel_ext is not None:
                    L.activated_floating_panel_ext.prepare()
                
                L.update() ; return
            
            if L.order_overlap == False or L.delete_mode == True: 
                r = clicked_on_trade
                ibapp.cancelOrder(r.id, OrderCancel())
                L.click_indicator.append([r, time.time(), 'delete'])
                
                if stp == True: # NOTE if trade gets "stuck" use ctrl + click
                    r.status = 'cancel_forced'
                    r not in (l:=r.inst.trades) or l.remove(r)

                L.update() ; return
        
        if on is None or L.delete_mode == True:
            # mouse press and location did not match a trade type
            return
        
        price_int = L.price_rows[row]
        
        if L.activated_floating_panel_ext is not None:
            trade = tws_Trade(T, price_int, on, stp, place = False)
            L.activated_floating_panel_ext.pass_trade(trade)
        else:
            # post the trade
            trade = tws_Trade(T, price_int, on, stp)

        L.update()

ladderex = widgetLadder()
sys.exit(qapp.exec())
