'use strict';
const link = document.createElement('a');
link.href = '/research'; link.textContent = '消息舆情 · 腾讯行情接入';
link.setAttribute('aria-label','打开消息、题材与共享行情配置');
Object.assign(link.style,{position:'fixed',bottom:'18px',right:'20px',zIndex:'30',padding:'10px 16px',borderRadius:'8px',background:'#153d37',color:'#bcfff0',border:'1px solid #5b9b8c',fontSize:'14px',textDecoration:'none',boxShadow:'0 3px 12px #0005'});
document.body.append(link);
