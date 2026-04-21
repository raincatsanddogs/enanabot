const { on } = require('process');
const { on_command } = require('../../handler/commandManager');
const config = require('../../configs/config');
const fs = require("fs")
const path = require("path")

const WORDLE_ECHO_PREFIX = (
    typeof config.command_echo_prefix === 'string' && config.command_echo_prefix.trim()
)
    ? config.command_echo_prefix.trim()
    : '__WORDLE_CMD__';

function wordle_reply(session, text) {
    session.bot.chat(`${WORDLE_ECHO_PREFIX} ${text}`);
}

//游戏状态全局变量
let gameActive = false;
let targetWord = '';
let attempts = 0;
const maxAttempts = 6;
let wordle_list = [];
let wordle_list_set = new Set();
let guess_list = [];
let guess_list_set = new Set();


const rows = 6;
const cols = 7;//目前还是5
const wordle_status = Array.from({ length: rows }, () => 
  Array.from({ length: cols }, () => ({ letter: '', status: '' }))
);

function reset_wordle_status() {
    for (let i = 0; i < wordle_status.length; i++) {
        for (let j = 0; j < wordle_status[i].length; j++) {
            wordle_status[i][j] = { letter: '', status: '' };
        }
    }
}

//猜词状态数组
/*
[
    [{letter: 'h', status: 'correct'},{letter: 'o', status: 'misplace'},{letter: 'l', status: 'correct'},{letter: 'l', status: 'correct'},{letter: 'l', status: 'incorrect'}],
    [],
    [],
    [],
    [],
    []
]

*/


const guess = on_command('guess', {permission: 'guest',description:'猜wordle词'} );
guess.handle(async (session) =>
{
    if (!gameActive) {
        wordle_reply(session, '没有正在进行中的wordle,请输入#wordle start开始游戏');
        // No active game
        return;
    }
    if (attempts >= maxAttempts) {
        gameActive = false;
        wordle_reply(session, `游戏结束！正确单词是${targetWord}`);
        return;
    }
    const arg = session.args[0];
    if (!arg) {
        wordle_reply(session, '干什么？！');
        return;
    }
    if (arg.length !== 5) {
        wordle_reply(session, '请输入一个五字单词');
        return;
    }
    if (!guess_list_set.has(arg)) {
        wordle_reply(session, '单词不在词库中，请输入一个有效的五字单词');
        return;
    }//以上为输入错误判断

    const guess_word_list = [...arg];
    const target_word_list = [...targetWord];
    for (let i = 0; i < target_word_list.length; i++) {
        if (guess_word_list[i] === target_word_list[i]) {
            wordle_status[attempts][i] = { letter: guess_word_list[i], status: 'correct' };
            target_word_list[i] = null;
        }
    }//第一轮循环处理正确位置的字母
    for (let i = 0; i < target_word_list.length; i++) {
        if (wordle_status[attempts][i].status === 'correct') {
            continue;
        }
        const index = target_word_list.indexOf(guess_word_list[i]);
        if (index !== -1) {
            wordle_status[attempts][i] = { letter: guess_word_list[i], status: 'misplace' };
            target_word_list[index] = null;
        } else {
            wordle_status[attempts][i] = { letter: guess_word_list[i], status: 'incorrect' };
        }   
    }//第二轮循环处理错误位置的字母和不在单词中的字母
    
    for (let i = 0; i < wordle_status.length; i++) {
        let turn_result = '';
        for (let j = 0; j < wordle_status[i].length; j++) {
            const { letter, status } = wordle_status[i][j];
            if (letter === '') {
                continue;
            } else if (status === 'correct') {
                turn_result += `&a${letter}&r`;
            } else if (status === 'misplace') {
                turn_result += `&e${letter}&r`;
            } else {
                turn_result += `&c${letter}&r`;
            }
        }
        if (turn_result.length > 0) {
            wordle_reply(session, turn_result);
        }
    }
    //输出猜词结果
    const if_guess_correct = arg === targetWord;
    if (if_guess_correct) {
        wordle_reply(session, `恭喜你猜对了！正确单词是${targetWord}`);
        gameActive = false;
        return;
    }
    if (attempts >= maxAttempts - 1) {
        wordle_reply(session, `游戏结束！正确单词是${targetWord}`);
        gameActive = false;
        return;
    }
    //以上为游戏结束判断
    
    attempts++;
});

const hint = on_command('hint', {permission: 'guest',description:'获取wordle提示'} );
hint.handle(async (session) =>
{   if (!gameActive) {
        wordle_reply(session, '没有正在进行中的wordle,请输入#wordle start开始游戏');
        // No active game
    } else if (attempts === 0) {
        wordle_reply(session, '你还没有进行过猜词，无法获取提示');
    }
    else {
        // Provide a hint based on the current game state
        wordle_reply(session, '提示功能先鸽着（');
    }
});


//初始化游戏
read_word_list();
const wordle = on_command('wordle', {permission: 'guest',description:'wordle游戏指令'} );
wordle.handle(async (session) =>{
    const args = session.args;
    switch (args[0]) {
        case 'start':
            if (!gameActive) {
                gameActive = true;
                targetWord = guess_list[getRndInteger(0, guess_list.length)];
                attempts = 0;
                reset_wordle_status();
                wordle_reply(session, 'wordle已开始，输入#guess <五字母单词>开始猜词');
            } else {
                wordle_reply(session, '已有进行中的wordle，输入#wordle stop可结束当前游戏');
            }
            break;
        case 'stop':
            if (gameActive) {
                gameActive = false;
                wordle_reply(session, `已结束当前wordle，答案是${targetWord}`);
            } else {
                wordle_reply(session, '当前没有正在进行中的wordle');
            }
            break;
        default:
            wordle_reply(session, '用法: #wordle <start|stop>');
            break;
    }
});

function read_word_list() {
    // Implementation for reading word list
    const data1 = fs.readFileSync(path.join(__dirname, 'wordle_list', 'filtered-wordle-words.txt'), 'utf8');
    wordle_list = data1.split('\n').map(word => word.trim().toLowerCase()).filter(word => word.length === 5);
    wordle_list_set = new Set(wordle_list);

    const data2 = fs.readFileSync(path.join(__dirname, 'wordle_list', 'words.txt'), 'utf8');
    guess_list = data2.split('\n').map(word => word.trim().toLowerCase()).filter(word => word.length === 5);
    guess_list_set = new Set(guess_list);

    return 0;
}

function getRndInteger(min, max) {
    return Math.floor(Math.random() * (max - min) ) + min;
}