<?php
require __DIR__.'/php_faraz_wallet_contract.php';
// The native sender is stubbed; no customer receives SMS from this contract.
check($wpdb->saved[str_repeat('a',64)]['sms_state']==='pending','Confirmed credit not queued');
check($wpdb->saved[str_repeat('b',64)]['sms_state']==='pending','Recovered credit not queued');
check($wpdb->saved[str_repeat('c',64)]['sms_state']==='awaiting_credit','Unknown credit notified');
$sms_calls=0; $sms_result='success'; $pattern='configured'; $attrs_seen=null;
function farazsms_wallet_topup_settings() { global $pattern; return array('pattern_code'=>$pattern); }
function farazsms_get_apikey() { return 'synthetic-not-a-secret'; }
function farazsms_wallet_topup_sms_attrs($amount,$balance) { return array('amount'=>(string)$amount,'balance'=>(string)$balance); }
function farazsms_queue_channel_sender($channel) { check($channel==='wallet-topup','Wrong native channel'); return 'native-line'; }
function farazsms_sms_send_pattern($phone,$pattern,$attrs,$sender) {
    global $wpdb,$sms_calls,$sms_result,$attrs_seen;
    check($phone==='09120000007' && $sender==='native-line','Native SMS routing');
    check($wpdb->saved[str_repeat('a',64)]['sms_state']==='sending','Send not persisted before provider call');
    $sms_calls++; $attrs_seen=$attrs;
    if ($sms_result==='throw') throw new RuntimeException('network uncertain');
    return $sms_result;
}
$key=str_repeat('a',64);
$pattern=''; Afrand_Faraz_Credit_Notifications::deliver($wpdb->saved[$key]);
check($sms_calls===0 && $wpdb->saved[$key]['sms_state']==='configuration','Missing config attempted send');
$pattern='configured'; Afrand_Faraz_Credit_Notifications::deliver($wpdb->saved[$key]);
check($sms_calls===1 && $wpdb->saved[$key]['sms_state']==='accepted' && $attrs_seen['amount']==='123','Notification not accepted');
$p['request_id']=$key; $p['currency']='IRT';
Afrand_Faraz_Wallet_Bridge::credit(new Request($p));
check($wpdb->saved[$key]['sms_state']==='accepted' && $sms_calls===1,'Replay reset notification');
$legacy=$wpdb->saved[$key]; unset($legacy['sms_state']); $legacy['state']='processing';
$wpdb->saved[$key]=$legacy;
Afrand_Faraz_Wallet_Bridge::reconcile($key,$legacy);
check(!isset($wpdb->saved[$key]['sms_state']),'Historical credit enrolled in notifications');
$row=$wpdb->saved[$key]; $row['sms_json']=json_encode(array('phone'=>'bad','amount'=>'123','balance'=>'0','currency'=>'IRT'));
Afrand_Faraz_Credit_Notifications::deliver($row);
check($sms_calls===1 && $wpdb->saved[$key]['sms_state']==='invalid_phone','Invalid phone sent');
foreach (array('opted_out'=>'opted_out','api_error:rejected'=>'rejected','curl_error:timeout'=>'unknown','throw'=>'unknown') as $response=>$expected) {
    $row['sms_json']=json_encode(array('phone'=>'09120000007','amount'=>'123','balance'=>'0','currency'=>'IRT'));
    $sms_result=$response;
    Afrand_Faraz_Credit_Notifications::deliver($row);
    check($wpdb->saved[$key]['sms_state']===$expected,'Wrong notification result: '.$response);
}
check($calls===3,'SMS sent another financial credit');

$allowed=true;
function current_user_can($capability) { global $allowed; return $allowed && $capability==='manage_woocommerce'; }
$handler=array('callback'=>array('Afrand_Faraz_Wallet_Bridge','credit'));
$response=(object)array('financial'=>'unchanged');
$request=new Request(array('request_id'=>$key));
$wpdb->saved[$key]=array_merge($row,array('state'=>'succeeded','sms_state'=>'pending'));
$before=$sms_calls; $sms_result='success';
foreach (array(
    array('_route'=>'/wc/v3/orders'), array('_method'=>'GET'), array('request_id'=>'bad')
) as $override) {
    check(Afrand_Faraz_Credit_Notifications::after_credit($response,$handler,new Request(array_merge(array('request_id'=>$key),$override)))===$response,'Changed unrelated response');
}
$allowed=false; Afrand_Faraz_Credit_Notifications::after_credit($response,$handler,$request); $allowed=true;
$error=new WP_Error('permission','Denied',array());
check(Afrand_Faraz_Credit_Notifications::after_credit($error,$handler,$request)===$error,'Changed permission error');
Afrand_Faraz_Credit_Notifications::after_credit($response,array('callback'=>'other_handler'),$request);
check($sms_calls===$before,'Unrelated or unauthorized request sent SMS');
check(Afrand_Faraz_Credit_Notifications::after_credit($response,$handler,$request)===$response,'Changed financial response');
check($sms_calls===$before+1 && $wpdb->saved[$key]['sms_state']==='accepted','Own pending notification not sent');
Afrand_Faraz_Credit_Notifications::after_credit($response,$handler,$request);
check($sms_calls===$before+1,'Replay sent a duplicate message');
$wpdb->saved[$key]['sms_state']='pending'; $sms_result='throw';
check(Afrand_Faraz_Credit_Notifications::after_credit($response,$handler,$request)===$response,'Provider exception changed financial response');
check($wpdb->saved[$key]['sms_state']==='unknown','Interrupted send not marked uncertain');
Afrand_Faraz_Credit_Notifications::after_credit($response,$handler,$request);
check($sms_calls===$before+2,'Uncertain send retried');
foreach (array('legacy','awaiting_credit','configuration','sending','rejected','opted_out') as $state) {
    $wpdb->saved[$key]['sms_state']=$state;
    Afrand_Faraz_Credit_Notifications::after_credit($response,$handler,$request);
}
check($sms_calls===$before+2 && $calls===3 && $wpdb->locks===0,'Isolation or worker lock failed');
echo "PASS: post-credit-only notification, authorization, unchanged response, replay, uncertain outcome, historical isolation, lock release (synthetic)\n";
echo "PASS: notification intent, recovery, replay, legacy isolation, config, phone, consent, rejection, timeout, exception, no extra credit (synthetic)\n";
