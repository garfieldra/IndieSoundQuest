package com.indiesoundquest.tournament.web;
import com.indiesoundquest.tournament.application.CandidatePoolContractException; import com.indiesoundquest.tournament.application.CandidatePoolUnavailableException; import com.indiesoundquest.tournament.application.IdempotencyKeyConflictException; import java.util.*; import org.springframework.http.*; import org.springframework.web.bind.MethodArgumentNotValidException; import org.springframework.web.bind.MissingRequestHeaderException; import org.springframework.web.bind.annotation.*;
@RestControllerAdvice public class ApiExceptionHandler {
 @ExceptionHandler(NoSuchElementException.class) ResponseEntity<Map<String,Object>> notFound(){return error(HttpStatus.NOT_FOUND,"RESOURCE_NOT_FOUND","资源不存在或不可访问");}
 @ExceptionHandler(SecurityException.class) ResponseEntity<Map<String,Object>> forbidden(){return error(HttpStatus.FORBIDDEN,"TOURNAMENT_ACCESS_DENIED","赛事不可访问");}
 @ExceptionHandler(IllegalArgumentException.class) ResponseEntity<Map<String,Object>> invalidArgument(IllegalArgumentException e){return error(HttpStatus.BAD_REQUEST,"VALIDATION_FAILED",e.getMessage());}
 @ExceptionHandler(IllegalStateException.class) ResponseEntity<Map<String,Object>> invalidState(IllegalStateException e){return error(HttpStatus.CONFLICT,"TOURNAMENT_STATE_INVALID",e.getMessage());}
 @ExceptionHandler(IdempotencyKeyConflictException.class) ResponseEntity<Map<String,Object>> idempotencyConflict(IdempotencyKeyConflictException e){return error(HttpStatus.CONFLICT,"IDEMPOTENCY_KEY_REUSED",e.getMessage());}
 @ExceptionHandler(CandidatePoolContractException.class) ResponseEntity<Map<String,Object>> candidateContract(CandidatePoolContractException e){return error(HttpStatus.BAD_GATEWAY,"CANDIDATE_POOL_CONTRACT_INVALID","候选歌曲结果未通过目录校验，请重新生成");}
 @ExceptionHandler(CandidatePoolUnavailableException.class) ResponseEntity<Map<String,Object>> candidateUnavailable(CandidatePoolUnavailableException e){return error(HttpStatus.SERVICE_UNAVAILABLE,"CANDIDATE_POOL_UNAVAILABLE",e.getMessage());}
 @ExceptionHandler({MethodArgumentNotValidException.class,MissingRequestHeaderException.class}) ResponseEntity<Map<String,Object>> validation(){return error(HttpStatus.BAD_REQUEST,"VALIDATION_FAILED","请求参数不合法");}
 private ResponseEntity<Map<String,Object>> error(HttpStatus s,String code,String message){return ResponseEntity.status(s).body(Map.of("code",code,"message",message,"requestId",UUID.randomUUID().toString()));}
}
