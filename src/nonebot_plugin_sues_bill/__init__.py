from nonebot import logger, require
from nonebot.plugin import PluginMetadata, inherit_supported_adapters
from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.params import CommandArg
import requests
import re
import pickle
from pathlib import Path
from PIL import Image
import io
from pytesseract import image_to_string

require("nonebot_plugin_uninfo")
require("nonebot_plugin_alconna")
require("nonebot_plugin_localstore")
require("nonebot_plugin_apscheduler")
from .config import Config

__plugin_meta__ = PluginMetadata(
    name="电费查询",
    description="sues账单查询插件",
    usage="见 #电费帮助",
    type="application",  # library
    homepage="https://github.com/noel-psc/nonebot-plugin-sues-bill",
    config=Config,
    supported_adapters=inherit_supported_adapters(
        "nonebot_plugin_alconna", "nonebot_plugin_uninfo"
    ),
    # supported_adapters={"~onebot.v11"}, # 仅 onebot
    extra={"author": "noel-psc <your@mail.com>"},
)

from arclet.alconna import Args, Option, Alconna, Arparma, Subcommand
from nonebot_plugin_alconna import on_alconna
from nonebot_plugin_alconna.uniseg import UniMessage

pip = on_alconna(
    Alconna(
        "pip",
        Subcommand(
            "install",
            Args["package", str],
            Option("-r|--requirement", Args["file", str]),
            Option("-i|--index-url", Args["url", str]),
        ),
    )
)


@pip.handle()
async def _(result: Arparma):
    package: str = result.other_args["package"]
    logger.info(f"installing {package}")
    await UniMessage.text(package).send()

# 配置
BASE_URL = 'https://epay.sues.edu.cn'
LOGIN_PATH = '/epay/person/index'
QUERY_PATH = '/epay/wxpage/wanxiao/eleresult'
HOME_PATH = '/'

# Cookie存储配置
COOKIE_DIR = Path(__file__).parent / '.cache'
COOKIE_FILE = COOKIE_DIR / 'cookie.enc'

# 用户数据存储（用户名和密码）
USER_DATA_FILE = COOKIE_DIR / 'user_data.pkl'

# 创建电费查询命令
electric_query = on_command("电费", priority=5, block=True)
electric_set_user = on_command("设置电费账号", priority=5, block=True)
electric_help = on_command("电费帮助", priority=5, block=True)

def ensure_cookie_dir():
    """确保cookie目录存在"""
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)

def load_cookie():
    """加载保存的cookie"""
    ensure_cookie_dir()
    if COOKIE_FILE.exists():
        try:
            with open(COOKIE_FILE, 'rb') as f:
                return pickle.load(f)
        except:
            return None
    return None

def save_cookie(cookies):
    """保存cookie"""
    ensure_cookie_dir()
    with open(COOKIE_FILE, 'wb') as f:
        pickle.dump(cookies, f)

def load_user_data():
    """加载用户数据"""
    ensure_cookie_dir()
    if USER_DATA_FILE.exists():
        try:
            with open(USER_DATA_FILE, 'rb') as f:
                return pickle.load(f)
        except:
            return {}
    return {}

def save_user_data(user_data):
    """保存用户数据"""
    ensure_cookie_dir()
    with open(USER_DATA_FILE, 'wb') as f:
        pickle.dump(user_data, f)

def recognize_captcha(image_content):
    """识别数字验证码"""
    try:
        img = Image.open(io.BytesIO(image_content))
        img = img.convert('L')
        img = img.point(lambda x: 0 if x < 128 else 255, '1')
        captcha = image_to_string(img, config='--psm 7 -c tessedit_char_whitelist=0123456789')
        captcha = captcha.strip()
        return captcha if captcha else None
    except Exception as e:
        print(f"验证码识别失败: {str(e)}")
        return None

def login(session, username, password):
    """登录系统"""
    try:
        login_url = BASE_URL + LOGIN_PATH
        response = session.get(login_url)
        
        # 提取CSRF Token
        csrf_token = None
        csrf_pattern = r'<meta name="_csrf" content="([^"]+)"/>'
        csrf_match = re.search(csrf_pattern, response.text)
        if csrf_match:
            csrf_token = csrf_match.group(1)
        
        # 提取验证码图片URL
        captcha_pattern = r'<img[^>]+src="([^"]*imageCode[^"]*)"'
        captcha_match = re.search(captcha_pattern, response.text)
        
        captcha = None
        if captcha_match:
            captcha_url = captcha_match.group(1)
            if not captcha_url.startswith('http'):
                captcha_url = BASE_URL + captcha_url
            
            captcha_response = session.get(captcha_url)
            captcha = recognize_captcha(captcha_response.content)
        
        # 提取登录表单
        form_pattern = r'<form[^>]+action="([^"]+)"'
        form_match = re.search(form_pattern, response.text)
        
        if form_match:
            form_action = form_match.group(1)
            if form_action.startswith('/'):
                form_action = BASE_URL + form_action
            elif not form_action.startswith('http'):
                form_action = BASE_URL + '/' + form_action
            
            # 提取所有输入字段
            input_pattern = r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"'
            input_matches = re.findall(input_pattern, response.text)
            
            # 构建表单数据
            form_data = {}
            for name, value in input_matches:
                form_data[name] = value
            
            # 添加用户名、密码和验证码
            form_data['j_username'] = username
            form_data['j_password'] = password
            if captcha:
                form_data['imageCodeName'] = captcha
            
            # 提交表单
            headers = {}
            if csrf_token:
                headers['X-CSRF-TOKEN'] = csrf_token
            
            login_response = session.post(form_action, data=form_data, headers=headers)
            
            # 检查是否登录成功
            if '登录' not in login_response.text and '错误' not in login_response.text:
                # 访问主页以建立会话
                home_url = BASE_URL + HOME_PATH
                session.get(home_url)
                
                # 保存cookie
                save_cookie(session.cookies.get_dict())
                return True
        return False
    except Exception as e:
        print(f"登录失败: {str(e)}")
        return False

def query_electric_bill(sysid, roomid, areaid, buildid, username=None, password=None):
    """查询宿舍电费信息"""
    try:
        session = requests.Session()
        
        # 尝试加载保存的cookie
        saved_cookies = load_cookie()
        if saved_cookies:
            session.cookies.update(saved_cookies)
        elif username and password:
            # 登录
            if not login(session, username, password):
                return {
                    'retcode': -1,
                    'retmsg': '登录失败，请检查用户名、密码或验证码'
                }
        else:
            return {
                'retcode': -1,
                'retmsg': '未设置账号信息，请先使用"设置电费账号"命令'
            }
        
        # 访问主页以建立会话
        home_url = BASE_URL + HOME_PATH
        session.get(home_url)
        
        # 发送查询电费的请求
        query_url = BASE_URL + QUERY_PATH
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0'
        }
        
        params = {
            'sysid': sysid,
            'roomid': roomid,
            'areaid': areaid,
            'buildid': buildid
        }
        
        query_response = session.get(query_url, params=params, headers=headers)
        
        # 从HTML中提取剩余电量
        pattern = r'(\d+\.?\d*)\s*度'
        match = re.search(pattern, query_response.text)
        
        if match:
            rest_degree = float(match.group(1))
            return {
                'retcode': 0,
                'retmsg': '成功',
                'restElecDegree': rest_degree
            }
        else:
            return {
                'retcode': -1,
                'retmsg': '未找到剩余电量信息'
            }
    except Exception as e:
        return {
            'retcode': -1,
            'retmsg': f'错误: {str(e)}'
        }

@electric_set_user.handle()
async def handle_set_user(bot: Bot, event: Event, args: Message = CommandArg()):
    """处理设置电费账号命令"""
    # 获取参数
    arg_text = args.extract_plain_text().strip()
    
    if not arg_text:
        await electric_set_user.finish("请提供用户名和密码，格式：#设置电费账号 用户名 密码")
    
    # 解析参数
    parts = arg_text.split()
    if len(parts) < 2:
        await electric_set_user.finish("参数不足，格式：#设置电费账号 用户名 密码")
    
    username = parts[0]
    password = parts[1]
    
    # 获取用户ID
    user_id = event.get_user_id()
    
    # 加载现有用户数据
    user_data = load_user_data()
    
    # 保存用户数据
    user_data[user_id] = {
        'username': username,
        'password': password
    }
    save_user_data(user_data)
    
    await electric_set_user.finish(f"账号设置成功！\n用户名: {username}\n现在可以使用“#电费”命令查询电费了")

@electric_query.handle()
async def handle_electric_query(bot: Bot, event: Event, args: Message = CommandArg()):
    """处理电费查询命令"""
    # 获取用户ID
    user_id = event.get_user_id()
    
    # 加载用户数据
    user_data = load_user_data()
    
    # 检查是否设置了账号
    if user_id not in user_data:
        await electric_query.finish("您还未设置账号，请先使用“#设置电费账号”命令设置账号\n格式：#设置电费账号 用户名 密码")
    
    # 获取用户账号信息
    username = user_data[user_id]['username']
    password = user_data[user_id]['password']
    
    # 获取参数
    arg_text = args.extract_plain_text().strip()
    
    # 解析参数
    parts = arg_text.split()
    if len(parts) >= 4:
        sysid = parts[0]
        roomid = parts[1]
        areaid = parts[2]
        buildid = parts[3]
    else:
        await electric_query.finish("参数不足，格式：#电费 [系统ID] [房间号] [区域ID] [楼栋ID]\n")
    
    # 查询电费
    result = query_electric_bill(
        sysid=sysid,
        roomid=roomid,
        areaid=areaid,
        buildid=buildid,
        username=username,
        password=password
    )
    
    if result.get('retcode') == 0:
        await electric_query.finish(f"电费查询成功！\n房间: {roomid}\n剩余电量: {result.get('restElecDegree', '未知')} 度")
    else:
        await electric_query.finish(f"查询失败: {result.get('retmsg', '未知错误')}")

@electric_help.handle()
async def handle_electric_help(bot: Bot, event: Event, args: Message = CommandArg()):
    await electric_query.finish("====电费查询帮助菜单====\n系统ID：\n  3：后勤部综合楼\n  4：上海工程技术大学电控充值\n区域ID：\n  101：三期学生公寓\n  102四期学生公寓\n  104：长宁南北宿舍楼\n  105：研究生一号楼9-11层\n  106：北区创客中心\n  107：长宁产教融合大楼\n  108：研究生宿舍楼\n楼栋ID：\n  2：三期10     3：三期11     4：三期12\n  5：三期13     6：三期14     7：三期15\n  8：三期16     9：三期17     10：三期18\n  11：三期19   12：三期20   13：三期21\n  14：三期22   15：三期23   16：三期24\n  17：三期25   18：三期26   19：四期20\n  20：四期21   21：四期23   22：四期24\n  23：四期27   24：四期28   25：四期29\n  26：四期30   27：四期33   28：四期34\n  29：四期35   30：四期36   31：四期39\n  32：四期40   33：四期41   34：四期42\n  35：南楼   36：北楼\n  38：研究生一号楼\n  39：创客中心\n  40：产教融合4-9楼\n  41：产教融合10-15楼\n  42：研究生二号楼1-6层\n  43：研究生一号楼1-4层\n  44：研究生二号楼7-12层\n  45：研究生一号楼5-8层\n")

