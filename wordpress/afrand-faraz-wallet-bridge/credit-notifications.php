<?php
/** Durable notifications for panel credits only; native online-order flows stay native. */
defined('ABSPATH') || exit;

final class Afrand_Faraz_Credit_Notifications {
    static function init() {
        if (get_option('afrand_faraz_bridge_schema') !== Afrand_Faraz_Wallet_Bridge::VERSION) Afrand_Faraz_Wallet_Bridge::activate();
        if (!wp_next_scheduled('afrand_faraz_credit_notifications')) wp_schedule_event(time()+30,'afrand_minute','afrand_faraz_credit_notifications');
    }
    static function schedules($schedules) {
        $schedules['afrand_minute']=array('interval'=>60,'display'=>'Afrand wallet notifications');
        return $schedules;
    }
    static function deactivate() { wp_clear_scheduled_hook('afrand_faraz_credit_notifications'); }
    static function labels() {
        return array('legacy'=>'—', 'awaiting_credit'=>'در انتظار تأیید واریز', 'pending'=>'در صف پیامک', 'configuration'=>'نیاز به تنظیم پیامک فراز', 'invalid_phone'=>'شماره موبایل نامعتبر', 'sending'=>'در حال ارسال', 'accepted'=>'پذیرفته‌شده توسط سامانه پیامک', 'opted_out'=>'عدم رضایت دریافت پیامک', 'rejected'=>'ردشده؛ گزارش پیامک فراز را بررسی کنید', 'unknown'=>'نتیجه ارسال نامشخص؛ قبل از ارسال مجدد بررسی شود');
    }
    static function update($key,$state,$data) {
        global $wpdb;
        return $wpdb->update(Afrand_Faraz_Wallet_Bridge::table(),array('sms_state'=>$state,'sms_json'=>wp_json_encode($data),'updated_at'=>current_time('mysql',true)),array('operation_key'=>$key)) !== false;
    }
    static function after_credit($response,$handler,$request) {
        // Run only after our authorized financial callback has released its locks.
        // A stalled WP-Cron must not leave new panel notifications waiting forever.
        if (is_wp_error($response) || !current_user_can('manage_woocommerce')
            || $request->get_route()!=='/wc/v3/afrand-faraz-wallet/credits'
            || $request->get_method()!=='POST'
            || !isset($handler['callback'])
            || $handler['callback']!==array('Afrand_Faraz_Wallet_Bridge','credit')) return $response;
        $key=$request->get_param('request_id');
        if (!is_string($key) || !preg_match('/^[a-f0-9]{64}$/',$key)) return $response;
        $locked=false;
        try {
            if (!Afrand_Faraz_Wallet_Bridge::supports_wallet_locks()) return $response;
            $locked=Afrand_Faraz_Wallet_Bridge::lock('sms-worker');
            if (!$locked) return $response;
            // Re-read under the same lock as cron; replays never resend accepted
            // or uncertain messages, nor enroll historical financial records.
            $row=Afrand_Faraz_Wallet_Bridge::saved($key);
            if ($row && $row['state']==='succeeded' && isset($row['sms_state']) && $row['sms_state']==='pending') self::deliver($row);
        } catch (Throwable $e) {
            // Financial success remains durable even when notification fails.
        } finally {
            if ($locked) Afrand_Faraz_Wallet_Bridge::unlock('sms-worker');
        }
        return $response;
    }
    static function deliver($row) {
        $key=$row['operation_key'];
        $data=json_decode($row['sms_json'],true);
        if (!is_array($data) || empty($data['phone']) || !preg_match('/^09\d{9}$/',$data['phone'])) { self::update($key,'invalid_phone',(array)$data); return; }
        $settings=function_exists('farazsms_wallet_topup_settings') ? farazsms_wallet_topup_settings() : array();
        $pattern=isset($settings['pattern_code']) ? trim($settings['pattern_code']) : '';
        // The native service enforces sender selection, consent, transport and diagnostic logs.
        if (!$pattern || !function_exists('farazsms_sms_send_pattern') || !function_exists('farazsms_wallet_topup_sms_attrs') || !function_exists('farazsms_get_apikey') || !farazsms_get_apikey() || !function_exists('get_woocommerce_currency') || $data['currency']!==get_woocommerce_currency()) { self::update($key,'configuration',$data); return; }
        $attrs=farazsms_wallet_topup_sms_attrs($data['amount'],$data['balance']);
        $sender=function_exists('farazsms_queue_channel_sender') ? farazsms_queue_channel_sender('wallet-topup') : '';
        // Persist before the network call. An interrupted/ambiguous send is never retried blindly.
        $data['attempted_at']=current_time('mysql',true);
        if (!self::update($key,'sending',$data)) return;
        try {
            $response=farazsms_sms_send_pattern($data['phone'],$pattern,$attrs,$sender);
            if ($response==='success') $state='accepted';
            elseif ($response==='opted_out') $state='opted_out';
            elseif (is_string($response) && (strpos($response,'api_error:')===0 || $response==='missing_params')) $state='rejected';
            else $state='unknown';
        } catch (Throwable $e) { $state='unknown'; }
        self::update($key,$state,$data);
    }
    static function run() {
        global $wpdb;
        if (!function_exists('farazsms_wallet_balance') || !function_exists('farazsms_wallet_table') || !Afrand_Faraz_Wallet_Bridge::supports_wallet_locks() || !Afrand_Faraz_Wallet_Bridge::lock('sms-worker')) return;
        try {
            $table=Afrand_Faraz_Wallet_Bridge::table();
            // Recover a committed credit whose HTTP response was lost, without issuing another credit.
            $rows=$wpdb->get_results("SELECT * FROM $table WHERE state='processing' AND sms_state='awaiting_credit' AND updated_at < UTC_TIMESTAMP() - INTERVAL 1 MINUTE ORDER BY updated_at LIMIT 5",ARRAY_A);
            foreach ((array)$rows as $row) {
                $lock='credit-'.$row['operation_key'];
                if (!Afrand_Faraz_Wallet_Bridge::lock($lock)) continue;
                try {
                    $fresh=Afrand_Faraz_Wallet_Bridge::saved($row['operation_key']);
                    if ($fresh && $fresh['state']==='processing') {
                        $result=Afrand_Faraz_Wallet_Bridge::reconcile($row['operation_key'],$fresh);
                        // Rotate unresolved operations so one missing ledger row cannot starve recovery.
                        if (is_wp_error($result)) $wpdb->update($table,array('updated_at'=>current_time('mysql',true)),array('operation_key'=>$row['operation_key']));
                    }
                }
                finally { Afrand_Faraz_Wallet_Bridge::unlock($lock); }
            }
            $wpdb->query("UPDATE $table SET sms_state='unknown' WHERE sms_state='sending' AND updated_at < UTC_TIMESTAMP() - INTERVAL 10 MINUTE");
            $rows=$wpdb->get_results("SELECT * FROM $table WHERE state='succeeded' AND (sms_state='pending' OR (sms_state='configuration' AND updated_at < UTC_TIMESTAMP() - INTERVAL 1 HOUR)) ORDER BY updated_at,operation_key LIMIT 10",ARRAY_A);
            $started=microtime(true);
            foreach ((array)$rows as $row) {
                if (microtime(true)-$started>10) break;
                self::deliver($row);
            }
        } finally { Afrand_Faraz_Wallet_Bridge::unlock('sms-worker'); }
    }
}
